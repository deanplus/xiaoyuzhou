#!/usr/bin/env python3
"""User-approved one-shot QR authentication, then bounded read-only probe.

Public sources: accounts.xiaoyuzhoufm.com/assets/index-CqItVqUi.js,
github.com/jerryqx/dsh-xiaoyuzhou/blob/main/lib/xyzfm.js,
github.com/HuangYincan/VideoNote-MCP/blob/main/videonote_mcp/cli.py.
This is an unofficial client of the first-party web login, not a registered OAuth app.
No existing credentials are read, no refresh token retained, no credentials saved.
"""
import argparse
from http.cookies import SimpleCookie, CookieError
import json
import os
from pathlib import Path
import resource
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
    token = headers.get("x-jike-access-token")
    if not token:
        # Only this new login response is inspected; never read browser storage.
        for value in headers.get_all("Set-Cookie", []):
            cookie = SimpleCookie()
            try:
                cookie.load(value)
            except CookieError:
                continue
            if "x-jike-access-token" in cookie:
                token = cookie["x-jike-access-token"].value
                break
    if not isinstance(token, str) or not 1 <= len(token) <= 8192 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise p.Stop("qr_confirmed_without_usable_access_token")
    return token


class QrLogin:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), p.NoRedirect())
        self.requests = 0

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
                    return data, access_from_headers(res.headers)
                return data, None
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                error = p.safe_http_error(status, exc.read(16385))
            except Exception:
                error = {"server_code": None}
            finally:
                exc.close()
            if status == 401 and error.get("server_code") == 21:
                raise p.Stop("qr_expired") from None
            raise p.Stop("qr_http_" + str(status)) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise p.Stop("qr_network_or_tls_error") from None


def render_qr(url, path):
    # Local PNG generation: no URL in argv, external services or logs.
    try:
        image = qrcode.make(url, image_factory=PyPNGImage, box_size=8, border=4)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
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


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-one-shot", action="store_true", help="Requires explicit user approval in the calling chat.")
    args = parser.parse_args()
    if not args.approved_one_shot:
        parser.print_help()
        return
    report = {"started_at": datetime.now(p.CN).isoformat(), "mode": "one_shot_qr_authenticated_probe",
              "authenticated": False, "credentials_saved": False, "refresh_used": False,
              "audio_played_or_downloaded": False, "mobile_history_sync": "not_verified",
              "local_disposal_does_not_revoke_server_session": True}
    login, client = QrLogin(), p.Client()
    handlers = {sig: signal.signal(sig, interrupt_for_cleanup) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
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
                data, access = login.post("/v1/auth/qrcode/login", {"id": qr_id})
                status = data.get("status") if isinstance(data, dict) else None
                if access:
                    client.token = access
                    del access, data, qr_id
                    qr_path.unlink()
                    report["authenticated"] = True
                    print("扫码登录已确认，开始只读验证。", flush=True)
                    p.run_probe(client, report)
                    report["verification"] = assess_report(report)
                    report["status"] = "read_paths_verified" if report["verification"]["all_read_paths_verified"] else "partial_reads"
                    break
                if status not in {"WAITTING", "SCANNED"}:
                    raise p.Stop("qr_unexpected_status")
            else:
                raise p.Stop("qr_timeout")
    except p.Stop as exc:
        report["status"] = str(exc)
    except (KeyboardInterrupt, EOFError):
        report["status"] = "cancelled"
    except Exception:
        report["status"] = "unexpected_error_details_suppressed"
    finally:
        report["qr_request_count"] = login.requests
        report["read_request_count"] = client.requests
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
