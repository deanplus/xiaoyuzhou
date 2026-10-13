"""Offline saved-session tests; all tokens and devices are fixed fake fixtures."""
import base64
import ctypes
from email.message import Message
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

import xyz_auth as a
import xyz_probe as p
import xyz_qr_probe as q

DEVICE = "00000000-0000-4000-8000-000000000001"
PAIR = {"access_token": "ACCESS_SENTINEL", "refresh_token": "REFRESH_SENTINEL"}
NEW_PAIR = {"access_token": "NEW_ACCESS_SENTINEL", "refresh_token": "NEW_REFRESH_SENTINEL"}


class Response:
    status = 200

    def __init__(self, data=None, pair=None):
        self.headers = Message()
        for key, value in (pair or {}).items():
            self.headers.add_header("x-jike-" + key.replace("_", "-"), value)
        self.body = json.dumps({} if data is None else data).encode()

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return self.body[:limit]


def http_error(code):
    return urllib.error.HTTPError(p.API, code, "fixture", Message(), io.BytesIO(b'{"message":"PRIVATE_SENTINEL"}'))


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sleep = patch("time.sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def saved(self):
        return a.SavedSession(self.root)

    def test_private_pair_round_trip_and_device_identity(self):
        with self.saved() as session:
            session.save_login(PAIR, DEVICE)
            path = session.path
            if os.name != "nt":
                self.assertEqual(session.directory.stat().st_mode & 0o777, 0o700)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(set(json.loads(path.read_text())),
                             {"version", "state", "access_token", "refresh_token", "device_id", "saved_at", "access_claims"})
        with self.saved() as session:
            client = p.Client()
            session.load(client)
            self.assertEqual(client.token, PAIR["access_token"])
            self.assertEqual(client.device_id, DEVICE)
            self.assertIs(client.saved_session, session)
        self.assertIsNone(session.credentials)

    def test_process_lock_and_cross_process_reuse(self):
        env = {**os.environ, "PYTHONPATH": str(p.BASE / "src")}
        code = '''import sys
from xyz_auth import SavedSession
from xyz_probe import Client, Stop
try:
    with SavedSession(sys.argv[1]) as session:
        client=Client(); session.load(client)
        assert client.token == "ACCESS_SENTINEL"
        assert client.device_id == "00000000-0000-4000-8000-000000000001"
        print("fixture_reused")
except Stop as exc:
    print(str(exc)); sys.exit(2)
'''
        with self.saved() as session:
            session.save_login(PAIR, DEVICE)
            busy = subprocess.run([sys.executable, "-B", "-c", code, str(self.root)], env=env,
                                  capture_output=True, text=True, timeout=10)
            self.assertEqual(busy.returncode, 2)
            self.assertIn("session_unavailable_or_in_use", busy.stdout)
        reuse = subprocess.run([sys.executable, "-B", "-c", code, str(self.root)], env=env,
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(reuse.returncode, 0, reuse.stderr)
        self.assertEqual(reuse.stdout.strip(), "fixture_reused")

    def test_symlinks_reparse_points_and_permissions_rejected(self):
        if os.name == "nt":
            self.skipTest("native Windows ACL test is separate; symlink privileges vary")
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "tmp").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(p.Stop, "unsafe_session_path"):
            with self.saved(): pass
        (self.root / "tmp").unlink()
        with self.saved() as session:
            session.save_login(PAIR, DEVICE)
            session.path.chmod(0o644)
            with self.assertRaisesRegex(p.Stop, "unsafe_session_permissions"):
                session.load(p.Client())
            session.path.unlink()
            session.path.symlink_to(outside / "missing")
            with self.assertRaisesRegex(p.Stop, "unsafe_session_path"):
                session.save_login(PAIR, DEVICE)
        fake = types.SimpleNamespace(st_mode=0, st_file_attributes=0x400)
        with patch.object(Path, "lstat", return_value=fake):
            self.assertTrue(a.link_like(self.root))

    def test_missing_corrupt_oversized_and_invalid_state_fail_safely(self):
        with self.saved() as session:
            with self.assertRaisesRegex(p.Stop, "session_missing_use_login"):
                session.load(p.Client())
            session.save_login(PAIR, DEVICE)
            for raw in (b'PRIVATE_SENTINEL', b'x' * 65537, b'[]', b'{"version":true}'):
                session.path.write_bytes(raw)
                with self.assertRaisesRegex(p.Stop, "session_file_invalid"):
                    session.load(p.Client())
            session.save_login(PAIR, DEVICE)
            data = json.loads(session.path.read_text())
            data["access_token"] = "INVALID\nPRIVATE_SENTINEL"
            session.path.write_text(json.dumps(data))
            with self.assertRaisesRegex(p.Stop, "^invalid_session_token$"):
                session.load(p.Client())

    def test_atomic_save_failure_preserves_previous_pair(self):
        with self.saved() as session:
            session.save_login(PAIR, DEVICE)
            before = session.path.read_bytes()
            with patch.object(a.os, "replace", side_effect=OSError("PRIVATE_SENTINEL")):
                with self.assertRaisesRegex(p.Stop, "^session_save_failed$"):
                    session.save_login(NEW_PAIR, DEVICE)
            self.assertEqual(session.path.read_bytes(), before)
            self.assertFalse(list(session.directory.glob("session-*.tmp")))

    def test_401_refresh_rotates_both_then_retries_with_stable_device(self):
        calls = []
        def open_request(req, **kwargs):
            calls.append(req)
            if len(calls) == 1: raise http_error(401)
            if len(calls) == 2: return Response(pair=NEW_PAIR)
            return Response({"data": []})
        with self.saved() as session:
            session.save_login(PAIR, DEVICE)
            client = p.Client()
            session.load(client)
            with patch.object(client.opener, "open", side_effect=open_request):
                result = client.request("POST", p.API + "/v1/episode-played/list-history", {}, authenticated=True)
            self.assertEqual(result, {"data": []})
            self.assertEqual([r.full_url for r in calls],
                             [p.API + "/v1/episode-played/list-history", a.REFRESH_URL,
                              p.API + "/v1/episode-played/list-history"])
            refresh_headers = dict(calls[1].header_items())
            self.assertEqual(refresh_headers["X-jike-refresh-token"], PAIR["refresh_token"])
            self.assertNotIn("X-jike-access-token", refresh_headers)
            retry_headers = dict(calls[2].header_items())
            self.assertEqual(retry_headers["X-jike-access-token"], NEW_PAIR["access_token"])
            self.assertEqual(retry_headers["X-jike-device-id"], DEVICE)
            data = json.loads(session.path.read_text())
            self.assertEqual(data["access_token"], NEW_PAIR["access_token"])
            self.assertEqual(data["refresh_token"], NEW_PAIR["refresh_token"])
            self.assertEqual(data["state"], "ready")
            self.assertEqual(client.requests, 3)
            self.assertEqual(client.refresh_requests, 1)
            self.assertTrue(client.refresh_succeeded)

    def test_second_401_cannot_loop_and_marks_relogin(self):
        with self.saved() as session:
            session.save_login(PAIR, DEVICE)
            client = p.Client()
            session.load(client)
            with patch.object(client.opener, "open", side_effect=[http_error(401), Response(pair=NEW_PAIR), http_error(401)]) as opened:
                for _ in range(2):
                    with self.assertRaisesRegex(p.Stop, "session_relogin_required"):
                        client.request("GET", p.API + "/v1/episode/get", authenticated=True)
                self.assertEqual(opened.call_count, 3)
            self.assertEqual(json.loads(session.path.read_text())["state"], "reauth_required")

    def test_network_429_5xx_and_auth_rejection_are_distinct(self):
        cases = [(urllib.error.URLError("PRIVATE_SENTINEL"), "refresh_network_or_tls_error", "refresh_pending"),
                 (http_error(429), "refresh_http_429", "ready"),
                 (http_error(500), "refresh_http_500", "refresh_pending"),
                 (http_error(401), "session_relogin_required", "reauth_required"),
                 (http_error(403), "session_relogin_required", "reauth_required")]
        for error, expected, state in cases:
            with self.subTest(expected=expected), self.saved() as session:
                session.save_login(PAIR, DEVICE)
                client = p.Client(); session.load(client)
                with patch.object(client.opener, "open", side_effect=[http_error(401), error]) as opened:
                    for _ in range(2):
                        with self.assertRaisesRegex(p.Stop, '^' + expected + '$'):
                            client.request("GET", p.API + "/v1/episode/get", authenticated=True)
                    self.assertEqual(opened.call_count, 2)
                data = json.loads(session.path.read_text())
                self.assertEqual(data["state"], state)
                self.assertEqual(data["refresh_token"], PAIR["refresh_token"])
                self.assertEqual(client.refresh_requests, 1)

    def test_rotation_save_failure_and_missing_refresh_block_old_pair(self):
        for missing in (False, True):
            with self.subTest(missing=missing), self.saved() as session:
                session.save_login(PAIR, DEVICE)
                client = p.Client(); session.load(client)
                pair = {"access_token": NEW_PAIR["access_token"]} if missing else NEW_PAIR
                with patch.object(client.opener, "open", side_effect=[http_error(401), Response(pair=pair)]):
                    if missing:
                        with self.assertRaisesRegex(p.Stop, "session_response_missing_tokens"):
                            client.request("GET", p.API + "/v1/episode/get", authenticated=True)
                    else:
                        with patch.object(session, "save_login", side_effect=p.Stop("session_save_failed")):
                            with self.assertRaisesRegex(p.Stop, "session_rotation_not_saved"):
                                client.request("GET", p.API + "/v1/episode/get", authenticated=True)
                self.assertEqual(json.loads(session.path.read_text())["state"], "refresh_pending")
                with self.assertRaisesRegex(p.Stop, "session_refresh_outcome_unknown"):
                    session.load(p.Client())

    def test_pending_guard_is_durable_before_network_and_no_send_if_write_fails(self):
        with self.saved() as session:
            session.save_login(PAIR, DEVICE)
            client = p.Client(); session.load(client)
            def refresh(req, **kwargs):
                self.assertEqual(json.loads(session.path.read_text())["state"], "refresh_pending")
                raise KeyboardInterrupt
            with patch.object(client.opener, "open", side_effect=refresh):
                with self.assertRaises(KeyboardInterrupt):session.refresh(client)
            with self.assertRaisesRegex(p.Stop, "session_refresh_outcome_unknown"):
                session.load(p.Client())
            session.save_login(PAIR, DEVICE)
            session.load(client)
            with patch.object(a.os, "replace", side_effect=OSError()), patch.object(client.opener, "open") as opened:
                with self.assertRaisesRegex(p.Stop, "session_save_failed"):
                    session.refresh(client)
                opened.assert_not_called()
            self.assertEqual(json.loads(session.path.read_text())["state"], "ready")

    def test_cdn_one_shot_and_bad_destinations_never_refresh(self):
        with self.saved() as session:
            session.save_login(PAIR, DEVICE)
            client = p.Client(); session.load(client)
            with patch.object(client.opener, "open", side_effect=http_error(401)) as opened:
                with self.assertRaisesRegex(p.Stop, "http_401"):
                    client.request("GET", "https://cdn.xyzcdn.net/fixture", cdn=True)
                headers = dict(opened.call_args.args[0].header_items())
                self.assertNotIn("X-jike-access-token", headers)
                self.assertNotIn("X-jike-refresh-token", headers)
            self.assertEqual(client.refresh_requests, 0)
            for url in (a.REFRESH_URL, "https://evil.test/app_auth_tokens.refresh", "http://api.xiaoyuzhoufm.com/v1/episode/get"):
                with self.assertRaises(p.Stop):client.request("POST", url, authenticated=True)
        client = p.Client(); client.token = PAIR["access_token"]
        with patch.object(client.opener, "open", side_effect=http_error(401)):
            with self.assertRaisesRegex(p.Stop, "http_401"):
                client.request("GET", p.API + "/v1/episode/get", authenticated=True)
        self.assertEqual(client.refresh_requests, 0)

    def test_refresh_budget_and_redirect_limits_preserve_tokens(self):
        with self.saved() as session:
            session.save_login(PAIR, DEVICE)
            client=p.Client();session.load(client);client.requests=10
            with patch.object(client.opener,"open") as opened:
                with self.assertRaisesRegex(p.Stop,"request_limit"):session.refresh(client)
                opened.assert_not_called()
            self.assertEqual(json.loads(session.path.read_text())["state"],"ready")
            client.requests=0
            with patch.object(client.opener,"open",side_effect=p.Stop("redirect_blocked")):
                with self.assertRaisesRegex(p.Stop,"redirect_blocked"):session.refresh(client)
            self.assertEqual(json.loads(session.path.read_text())["state"],"refresh_pending")

    def test_cookie_and_body_pair_validation_and_claims_only(self):
        headers=Message()
        for key,value in PAIR.items():headers.add_header("Set-Cookie", "x-jike-"+key.replace('_','-')+'='+value+'; Path=/')
        self.assertEqual(a.response_pair(headers),PAIR)
        body={"x-jike-"+k.replace('_','-'):v for k,v in PAIR.items()}
        self.assertEqual(a.response_pair(Message(),body),PAIR)
        for bad in (None,"", "x\ny", "é", "x"*8193):
            with self.assertRaises(p.Stop):a.checked_token(bad)
        payload=base64.urlsafe_b64encode(json.dumps({"iat":1600000000,"exp":1600007200,"uid":"PRIVATE_SENTINEL"}).encode()).decode().rstrip('=')
        claims=a.access_claims('fixture.'+payload+'.signature')
        self.assertEqual(claims,{"iat":1600000000,"exp":1600007200})
        self.assertEqual(a.access_claims("opaque-fixture"),{})

    def test_logout_deletes_credentials_but_keeps_lock_inode(self):
        with self.saved() as session:
            session.save_login(PAIR,DEVICE)
            session.logout();session.logout()
            self.assertFalse(session.path.exists())
            self.assertTrue((session.directory/"session.lock").exists())
        with self.assertRaisesRegex(p.Stop,"session_lock_required"):
            session.save_login(PAIR,DEVICE)

    def test_persistent_qr_requires_both_tokens_without_changing_one_shot(self):
        for persistent in (False,True):
            client=q.QrLogin(retain_refresh=persistent)
            response=Response({"status":"CONFIRMED"}, {"access_token":PAIR["access_token"]})
            with patch.object(client.opener,"open",return_value=response):
                if persistent:
                    with self.assertRaisesRegex(p.Stop,"session_response_missing_tokens"):client.post("/v1/auth/qrcode/login",{})
                else:self.assertEqual(client.post("/v1/auth/qrcode/login",{})[1],PAIR["access_token"])

    def test_cli_login_reuse_logout_and_reports_never_contain_tokens(self):
        reports=[]
        def post(path,body):
            if path.endswith('create'):return {"id":"fixture","url":"https://h5.xiaoyuzhoufm.com/oauth?id=fixture"},None
            return {"status":"CONFIRMED"},PAIR
        def probe(client,report):
            self.assertEqual(client.token,PAIR["access_token"])
            report.update(history_pages=[{}],playback_progress={"items":[{"played_at":{"utc_plus_8":"fixture"}}]},
                          transcript={"status":"available","text_segments":1})
        with patch.object(p,"BASE",self.root), patch.object(p,"save_report",side_effect=lambda r,n: reports.append(r) or Path('fixture')), \
             patch.object(p,"run_probe",side_effect=probe), patch("builtins.print"):
            with patch.object(q.sys,"argv",['probe','--login']),patch.object(q.QrLogin,"post",side_effect=post), \
                 patch.object(q,"render_qr",side_effect=lambda url,path:path.write_bytes(b'fixture')):
                self.assertEqual(q.main(),0)
            with patch.object(q.sys,"argv",['probe','--reuse-session']),patch.object(q.QrLogin,"post",side_effect=AssertionError('must not login')):
                self.assertEqual(q.main(),0)
            self.assertTrue(all(r['credentials_saved'] for r in reports))
            self.assertNotIn('SENTINEL',json.dumps(reports))
            with patch.object(q.sys,"argv",['probe','--logout']),patch.object(q.QrLogin,"post",side_effect=AssertionError('must not login')):
                self.assertEqual(q.main(),0)
            self.assertFalse((self.root/'tmp/auth/session.json').exists())

    def test_one_shot_never_opens_saved_session_and_reuse_never_creates_qr(self):
        with patch.object(q.sys,"argv",['probe','--approved-one-shot']),patch.object(a,"SavedSession",side_effect=AssertionError('must not read session')), \
             patch.object(q,"login_and_probe",side_effect=p.Stop("qr_timeout")),patch.object(p,"save_report",return_value=Path('fixture')),patch('builtins.print'):
            self.assertEqual(q.main(),2)
        with patch.object(p,"BASE",self.root),patch.object(q.sys,"argv",['probe','--reuse-session']), \
             patch.object(q.QrLogin,"post",side_effect=AssertionError('must not create QR')),patch.object(p,"save_report",return_value=Path('fixture')),patch('builtins.print'):
            self.assertEqual(q.main(),2)

    def test_windows_lock_branch_uses_nonblocking_byte_region(self):
        session=self.saved()
        fake=types.SimpleNamespace(LK_NBLCK=2,locking=MagicMock())
        with patch.object(a.os,"name","nt"),patch.dict(sys.modules,{"msvcrt":fake}),patch.object(a,"protect_path"):
            with session:
                self.assertEqual(os.lseek(session.lock.fileno(),0,os.SEEK_CUR),0)
                fake.locking.assert_called_once_with(session.lock.fileno(),2,1)
        self.assertIsNone(session.lock)

    def test_core_dump_protection_without_posix_resource(self):
        with patch.dict(sys.modules,{"resource":None}):p.disable_core_dumps()

    def test_windows_dacl_branch_uses_protected_current_user_and_system(self):
        api,kernel=MagicMock(),MagicMock()
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.return_value=1
        api.SetFileSecurityW.return_value=1
        with patch.object(a.os,"name","nt"),patch.object(ctypes,"WinDLL",side_effect=[api,kernel],create=True),\
             patch('subprocess.check_output',return_value=b'"fixture\\user","S-1-5-21-1"\n'):
            a.protect_path(str(self.root),directory=True)
        sddl=api.ConvertStringSecurityDescriptorToSecurityDescriptorW.call_args.args[0]
        self.assertEqual(sddl,'D:P(A;OICI;FA;;;S-1-5-21-1)(A;OICI;FA;;;SY)')
        self.assertEqual(api.SetFileSecurityW.call_args.args[1],0x4|0x80000000)
        kernel.LocalFree.assert_called_once()


if __name__=='__main__':unittest.main()
