import json
import unittest
from email.message import Message
import io
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import urllib.error

import xyz_probe as p
import xyz_qr_probe as q
import png


class QrSafetyTests(unittest.TestCase):
    def test_local_qr_png_is_private_and_has_quiet_zone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "login.png"
            q.render_qr("https://h5.xiaoyuzhoufm.com/oauth?id=fixture", path)
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            width, height, rows, metadata = png.Reader(bytes=path.read_bytes()).read()
            pixels = list(rows)
            self.assertEqual(width, height)
            self.assertTrue(metadata["greyscale"])
            white = (1 << metadata["bitdepth"]) - 1
            self.assertTrue(all(value == white for row in pixels[:32] + pixels[-32:] for value in row))
            self.assertTrue(all(value == white for row in pixels for value in list(row[:32]) + list(row[-32:])))
            self.assertTrue(any(value == 0 for row in pixels[32:-32] for value in row))

    def test_qr_render_failure_is_safe_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "login.png"
            path.write_bytes(b"existing fixture")
            with self.assertRaisesRegex(p.Stop, "^qr_render_failed$"):
                q.render_qr("https://h5.xiaoyuzhoufm.com/oauth?id=fixture", path)
            self.assertEqual(path.read_bytes(), b"existing fixture")
            path.unlink()
            with self.assertRaisesRegex(p.Stop, "^qr_render_failed$"):
                q.render_qr("PRIVATE_FIXTURE" * 1000, path)
            self.assertFalse(path.exists())

    def test_only_official_https_qr_destinations(self):
        self.assertEqual(q.checked_qr_payload({"id": "fixture", "url": "https://h5.xiaoyuzhoufm.com/oauth?id=fixture"})[0], "fixture")
        for url in ["https://h5.xiaoyuzhoufm.com.evil.test/oauth", "http://h5.xiaoyuzhoufm.com/oauth",
                    "https://user@h5.xiaoyuzhoufm.com/oauth", "https://h5.xiaoyuzhoufm.com:444/oauth",
                    "https://h5.xiaoyuzhoufm.com:bad/oauth", "cosmos://login", "https://h5.xiaoyuzhoufm.com/\n"]:
            with self.assertRaises(p.Stop):
                q.checked_qr_payload({"id": "fixture", "url": url})

    def test_new_response_access_only_and_no_cookie_persistence(self):
        headers = Message()
        headers.add_header("Set-Cookie", "x-jike-refresh-token=REFRESH_SENTINEL; Path=/")
        headers.add_header("Set-Cookie", "x-jike-access-token=ACCESS_SENTINEL; Path=/; Secure")
        self.assertEqual(q.access_from_headers(headers), "ACCESS_SENTINEL")
        self.assertFalse(hasattr(q.QrLogin(), "cookies"))
        headers = Message()
        headers.add_header("x-jike-access-token", "ACCESS_SENTINEL")
        self.assertEqual(q.access_from_headers(headers), "ACCESS_SENTINEL")
        with self.assertRaises(p.Stop):
            q.access_from_headers(Message())

    def test_other_auth_endpoints_blocked_before_network(self):
        client = q.QrLogin()
        for path in ["/v1/auth/send-code", "/app_auth_tokens.refresh", "https://example.com"]:
            with self.assertRaises(p.Stop):
                client.post(path, {})
        self.assertEqual(client.requests, 0)

    def test_nested_progress_and_no_private_fields_exported(self):
        def fake(method, url, payload=None, **kwargs):
            if "list-history" in url:
                return {"data": [{"episode": {"eid": "a" * 24}}]}
            if "playback-progress/list" in url:
                return {"data": {"data": [{"eid": "a" * 24, "playedAt": "2026-10-12T16:10:00Z",
                                          "progress": 100, "uid": "PRIVATE_SENTINEL"}]}}
            return {"data": {"eid": p.TARGET}}
        client = p.Client()
        client.request = fake
        report = {}
        p.run_probe(client, report)
        self.assertEqual(report["playback_progress"]["items"][0]["played_at"]["utc_plus_8"], "2026-10-13T00:10:00+08:00")
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(report))

    def test_optional_history_or_progress_failure_still_tests_transcript(self):
        for failure in ("list-history", "playback-progress/list"):
            calls = []
            def fake(method, url, payload=None, **kwargs):
                calls.append(url)
                if failure in url:
                    raise p.Stop("http_404")
                if "list-history" in url:
                    return {"data": [{"episode": {"eid": "a" * 24}}]}
                if "episode/get" in url:
                    return {"data": {"eid": p.TARGET, "transcriptMediaId": "fixture"}}
                if "episode-transcript/get" in url:
                    return {"data": {"transcriptUrl": "https://media.xyzcdn.net/fixture"}}
                return [{"text": "fixture", "startMs": 0}]
            client = p.Client()
            client.request = fake
            report = {}
            p.run_probe(client, report)
            self.assertEqual(report["transcript"]["status"], "available")
            self.assertIn(p.API + "/v1/episode-transcript/get", calls)

    def test_empty_transcript_and_nonfinite_values_cannot_mean_success(self):
        self.assertEqual(p.transcript_metadata([], 10)["status"], "empty_text")
        for value in (float("nan"), float("inf")):
            self.assertNotIn("raw", p.temporal_value(value))
            metadata = p.transcript_metadata([{"text": "fixture", "startMs": value}], value)
            json.dumps(metadata, allow_nan=False)
        self.assertFalse(q.assess_report({"transcript": {"status": "available", "text_segments": 0}})["all_read_paths_verified"])

    def test_qr_expiry_and_failed_error_body_read(self):
        for body, expected in [(io.BytesIO(b'{"code":21}'), "qr_expired"), (None, "qr_http_401")]:
            err = urllib.error.HTTPError(q.WEB, 401, "fixture", Message(), body)
            if body is None:
                err.read = lambda *args: (_ for _ in ()).throw(OSError())
            client = q.QrLogin()
            with patch.object(client.opener, "open", side_effect=err), self.assertRaisesRegex(p.Stop, expected):
                client.post("/v1/auth/qrcode/login", {"id": "fixture"})

    def test_confirmed_without_token_fails_at_response_boundary(self):
        class Response:
            headers = Message()
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return b'{"status":"CONFIRMED"}'
        client = q.QrLogin()
        with patch.object(client.opener, "open", return_value=Response()), self.assertRaisesRegex(p.Stop, "qr_confirmed_without_usable_access_token"):
            client.post("/v1/auth/qrcode/login", {"id": "fixture"})

    def test_main_confirmation_and_timeout_cleanup_exit_code(self):
        for confirmed in (True, False):
            created_paths, reports = [], []
            def render(url, path):
                path.write_bytes(b"offline-fixture")
                created_paths.append(path)
            def post(path, body):
                if path.endswith("create"):
                    return {"id": "fixture", "url": "https://h5.xiaoyuzhoufm.com/oauth?id=fixture"}, None
                return {"status": "CONFIRMED" if confirmed else "WAITTING"}, "ACCESS_SENTINEL" if confirmed else None
            def probe(client, report):
                self.assertFalse(created_paths[0].exists())
                self.assertEqual(client.token, "ACCESS_SENTINEL")
                report.update(history_pages=[{}], playback_progress={"items": [{"played_at": {"utc_plus_8": "fixture"}}]},
                              transcript={"status": "available", "text_segments": 1})
            def save(report, name):
                reports.append(report)
                return Path("offline-report")
            clock = [0, 0, 241] if not confirmed else [0, 0]
            with patch.object(q.QrLogin, "post", side_effect=post), patch.object(q, "render_qr", side_effect=render), \
                 patch.object(q.time, "sleep"), patch.object(q.time, "monotonic", side_effect=clock), \
                 patch.object(p, "run_probe", side_effect=probe), patch.object(p, "save_report", side_effect=save), \
                 patch.object(q.sys, "argv", ["probe", "--approved-one-shot"]), patch("builtins.print"):
                code = q.main()
            self.assertEqual(code, 0 if confirmed else 2)
            self.assertEqual(reports[0]["status"], "read_paths_verified" if confirmed else "qr_timeout")
            self.assertFalse(created_paths[0].parent.exists())
            self.assertNotIn("ACCESS_SENTINEL", json.dumps(reports[0]))


if __name__ == "__main__":
    unittest.main()
