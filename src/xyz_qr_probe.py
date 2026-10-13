#!/usr/bin/env python3
"""QR login, reusable local session and bounded read probe.

Public sources: accounts.xiaoyuzhoufm.com/assets/index-CqItVqUi.js,
github.com/jerryqx/dsh-xiaoyuzhou/blob/main/lib/xyzfm.js,
github.com/HuangYincan/VideoNote-MCP/blob/main/videonote_mcp/cli.py.
This is an unofficial client of the first-party web login, not a registered OAuth app.
One-shot mode never reads or saves credentials; persistent modes are explicit.
"""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import qrcode
from qrcode.image.pure import PyPNGImage

import xyz_probe as p
import xyz_auth as auth

WEB = "https://web-api.xiaoyuzhoufm.com"
MAX_POLLS = 120


def checked_qr_payload(value):
    if not isinstance(value, dict):
        raise p.Stop("unexpected_qr_shape")
    qr_id, url = value.get("id"), value.get("url")
    if not isinstance(qr_id, str) or not 1 <= len(qr_id) <= 256 or any(ord(c) < 33 for c in qr_id):
        raise p.Stop("invalid_qr_id")
    if not isinstance(url, str) or not 1 <= len(url) <= 4096 or any(ord(c) < 33 for c in url):
        raise p.Stop("invalid_qr_url")
    try:
        parsed = urllib.parse.urlsplit(url)
        safe = (parsed.scheme == "https" and parsed.hostname in {"h5.xiaoyuzhoufm.com", "accounts.xiaoyuzhoufm.com"}
                and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        safe = False
    if not safe:
        raise p.Stop("unexpected_qr_destination")
    return qr_id, url


def access_from_headers(headers):
    try:
        token = auth.response_token(headers, "x-jike-access-token")
        return auth.checked_token(token)
    except p.Stop:
        raise p.Stop("qr_confirmed_without_usable_access_token") from None


class QrLogin:
    def __init__(self, retain_refresh=False):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), p.NoRedirect())
        self.requests = 0
        self.retain_refresh = retain_refresh

    def post(self, path, body):
        if path not in {"/v1/auth/qrcode/create", "/v1/auth/qrcode/login"}:
            raise p.Stop("qr_endpoint_not_allowed")
        if self.requests >= MAX_POLLS + 1:
            raise p.Stop("qr_request_limit")
        req = urllib.request.Request(WEB + path, data=json.dumps(body).encode(), method="POST", headers={
            "Accept": "application/json", "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://accounts.xiaoyuzhoufm.com", "Referer": "https://accounts.xiaoyuzhoufm.com/login",
            "User-Agent": "Mozilla/5.0", "x-midway-app-id": "v6worU4NnWyL",
            "x-jike-allow-app-token-in-cookie": "true"})
        self.requests += 1
        try:
            with self.opener.open(req, timeout=10) as res:
                body = res.read(65537)
                if len(body) > 65536:
                    raise p.Stop("qr_response_too_large")
                try:
                    data = json.loads(body)
                except (ValueError, UnicodeError):
                    raise p.Stop("qr_non_json") from None
                if path.endswith("/login") and isinstance(data, dict) and data.get("status") in {"CONFIRMED", "USED"}:
                    tokens = auth.response_pair(res.headers, data) if self.retain_refresh else access_from_headers(res.headers)
                    return data, tokens
                return data, None
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                error = p.safe_http_error(status, exc.read(16385))
            except Exception:
                error = {"server_code": None}
            finally:
                p.close_http_error(exc)
            if status == 401 and error.get("server_code") == 21:
                raise p.Stop("qr_expired") from None
            raise p.Stop("qr_http_" + str(status)) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise p.Stop("qr_network_or_tls_error") from None


def render_qr(url, path):
    # Local PNG generation: no URL in argv, external services or logs.
    try:
        auth.protect_path(path.parent, directory=True)
        image = qrcode.make(url, image_factory=PyPNGImage, box_size=8, border=4)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            auth.protect_path(path)
            image.save(output)
    except Exception:
        raise p.Stop("qr_render_failed") from None


def assess_report(report):
    progress = report.get("playback_progress", {}).get("items", [])
    matched = sum("utc_plus_8" in row.get("played_at", {}) for row in progress)
    transcript = report.get("transcript", {})
    result = {"history_readable": bool(report.get("history_pages")),
              "history_item_count": len(report.get("history_items", [])),
              "played_at_matched_count": matched,
              "transcript_readable": transcript.get("status") == "available" and transcript.get("text_segments", 0) > 0}
    result["all_read_paths_verified"] = result["history_readable"] and matched > 0 and result["transcript_readable"]
    return result


def interrupt_for_cleanup(signum, frame):
    raise KeyboardInterrupt


def login_and_probe(login, client, report, saved_session=None):
    with tempfile.TemporaryDirectory(prefix="xyz-login-qr-") as tmp:
        qr_path = Path(tmp) / "login.png"
        payload, _ = login.post("/v1/auth/qrcode/create", {"clientId": "xyz-web"})
        qr_id, qr_url = checked_qr_payload(payload)
        render_qr(qr_url, qr_path)
        del payload, qr_url
        print("QR_IMAGE=" + str(qr_path), flush=True)
        print("请用小宇宙 App 扫一扫并确认；最多等候四分钟。", flush=True)
        deadline = time.monotonic() + 240
        for _ in range(MAX_POLLS):
            if time.monotonic() >= deadline:
                raise p.Stop("qr_timeout")
            time.sleep(2)
            data, tokens = login.post("/v1/auth/qrcode/login", {"id": qr_id})
            status = data.get("status") if isinstance(data, dict) else None
            if tokens:
                qr_path.unlink()
                report["authenticated"] = True
                if saved_session is not None:
                    saved_session.save_login(tokens, client.device_id)
                    saved_session.attach(client)
                    report["credentials_saved"] = True
                else:
                    client.token = tokens
                del tokens, data, qr_id
                print("扫码登录已确认，开始读取验证。", flush=True)
                p.run_probe(client, report)
                return
            if status not in {"WAITTING", "SCANNED"}:
                raise p.Stop("qr_unexpected_status")
        raise p.Stop("qr_timeout")


def main():
    p.disable_core_dumps()
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--approved-one-shot", action="store_true", help="User-approved QR test; no saved session read or written.")
    modes.add_argument("--login", action="store_true", help="QR login and save access/refresh tokens in local tmp/auth.")
    modes.add_argument("--reuse-session", action="store_true", help="Use local session; refresh once on API 401 without QR login.")
    modes.add_argument("--logout", action="store_true", help="Delete local credentials; does not revoke the server session.")
    args = parser.parse_args()
    if not any(vars(args).values()):
        parser.print_help()
        return 0
    if args.logout:
        try:
            with auth.SavedSession() as session:
                session.logout()
        except p.Stop as exc:
            print("状态：" + str(exc))
            return 2
        print("本地凭据已删除；服务端会话未撤销。")
        return 0
    report = {"started_at": datetime.now(p.CN).isoformat(),
              "mode": "one_shot_qr_authenticated_probe" if args.approved_one_shot else "saved_session_probe",
              "authenticated": False, "credentials_saved": False, "refresh_used": False,
              "audio_played_or_downloaded": False, "mobile_history_sync": "not_verified",
              "local_disposal_does_not_revoke_server_session": True}
    login, client = QrLogin(retain_refresh=args.login), p.Client()
    cleanup_signals = [getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)]
    handlers = {sig: signal.signal(sig, interrupt_for_cleanup) for sig in cleanup_signals}
    try:
        context = nullcontext() if args.approved_one_shot else auth.SavedSession()
        with context as session:
            if args.reuse_session:
                session.load(client)
                report["authenticated"] = True
                report["credentials_saved"] = True
                p.run_probe(client, report)
            else:
                login_and_probe(login, client, report, session)
            report["verification"] = assess_report(report)
            report["status"] = "read_paths_verified" if report["verification"]["all_read_paths_verified"] else "partial_reads"
    except p.Stop as exc:
        report["status"] = str(exc)
    except (KeyboardInterrupt, EOFError):
        report["status"] = "cancelled"
    except Exception:
        report["status"] = "unexpected_error_details_suppressed"
    finally:
        report["qr_request_count"] = login.requests
        report["read_request_count"] = client.requests - client.refresh_requests
        report["refresh_request_count"] = client.refresh_requests
        report["refresh_used"] = client.refresh_requests > 0
        report["refresh_succeeded"] = client.refresh_succeeded
        report["auth_error"] = client.auth_failure
        report["stage"] = client.stage
        if client.last_http_error is not None:
            report["http_error"] = client.last_http_error
        client.clear()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    report["verification"] = assess_report(report)
    name = "xyz_qr_result_" + datetime.now(p.CN).strftime("%Y%m%d_%H%M%S") + ".json"
    print("本地验证报告（含收听历史，无凭据）：" + str(p.save_report(report, name)), flush=True)
    print("状态：" + report["status"], flush=True)
    return 0 if report["status"] == "read_paths_verified" else 2


if __name__ == "__main__":
    sys.exit(main())
