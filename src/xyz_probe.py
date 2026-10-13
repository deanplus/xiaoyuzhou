#!/usr/bin/env python3
"""One-shot, standard-library-only Xiaoyuzhou feasibility probe.

No existing credentials, token files, refresh calls, audio downloads, history
writes, redirects, environment proxies, or transcript text exports.
Sources (reviewed 2026-10-12; unofficial and not a contract):
https://github.com/ultrazg/xyz/blob/main/handlers/history.go
https://github.com/r266-tech/xiaoyuzhou/blob/main/xiaoyuzhou/client.py
Progress-read source: https://github.com/jerryqx/dsh-xiaoyuzhou/blob/main/lib/xyzfm.js
"""
import argparse
import getpass
import json
import math
import os
import re
import resource
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.xiaoyuzhoufm.com"
AUTH = "https://podcaster-api.xiaoyuzhoufm.com"
TARGET = "6ac75a2ae742e36efcbfc909"
CN = timezone(timedelta(hours=8))
BASE = Path(__file__).resolve().parent.parent
LIMIT = 8 * 1024 * 1024
ALLOWED = {
    ("POST", AUTH + "/v1/auth/send-code"),
    ("POST", AUTH + "/v1/auth/login-with-sms"),
    ("POST", API + "/v1/episode-played/list-history"),
    ("POST", API + "/v1/playback-progress/list"),
    ("GET", API + "/v1/episode/get"),
    ("POST", API + "/v1/episode-transcript/get"),
}
SECRET = re.compile(r"token|secret|password|phone|mobile|verifycode|cookie|authorization|uid|device|email|nickname|user", re.I)
TEMPORAL = re.compile(r"(?:At|Date|Time|Timestamp)$|^(?:time|timestamp|date)$", re.I)
PUBLIC_KEYS = {"eid", "title", "duration", "isPlayed", "isFinished", "transcriptMediaId"}


class Stop(Exception):
    """Only constant, non-sensitive codes may be used as messages."""


def normalize_area_code(value):
    """Match the official form: blank local prompt defaults +86; add plus."""
    area = value.strip() or "+86"
    if not area.startswith("+"):
        area = "+" + area
    if not re.fullmatch(r"\+[1-9][0-9]{0,3}", area):
        raise Stop("invalid_area_code_format")
    return area


def safe_http_error(status, body):
    """Never retain arbitrary server text, headers, URLs or scalar fields."""
    result = {"http_status": status, "server_code": None,
              "server_message": None, "unrecognized_message_withheld": False}
    if len(body) > 16384:
        return {**result, "body_not_parsed": "size_limit"}
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        return {**result, "body_not_parsed": "not_json"}
    if not isinstance(data, dict):
        return {**result, "body_not_parsed": "not_object"}
    # Exact known-safe messages only: no substring extraction or redaction guess.
    messages = {"验证码错误", "请求参数错误", "请检查手机号格式", "验证码已过期",
                "请求过于频繁，请稍后再试", "需要验证", "Bad Request",
                "Invalid phone number", "Invalid area code", "CAPTCHA required"}
    codes = {"INVALID_PHONE_NUMBER", "INVALID_AREA_CODE", "INVALID_ARGUMENT",
             "CAPTCHA_REQUIRED", "CAPTCHA_INVALID", "RATE_LIMITED"}
    for node in (data, data.get("error"), data.get("data")):
        if not isinstance(node, dict):
            continue
        for key in ("code", "errorCode"):
            code = node.get(key)
            if type(code) is int and 0 <= code <= 9999:
                result["server_code"] = code
            elif isinstance(code, str) and code in codes:
                result["server_code"] = code
        for key in ("message", "msg", "toast", "error"):
            message = node.get(key)
            if isinstance(message, str):
                if message in messages:
                    result["server_message"] = message
                else:
                    result["unrecognized_message_withheld"] = True
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Stop("redirect_blocked")


def safe_schema(value, prefix="$", out=None, depth=0):
    """Record raw field paths/types, never arbitrary scalar values."""
    out = {} if out is None else out
    if depth > 14:
        return out
    out.setdefault(prefix, set()).add(type(value).__name__)
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", k) and not SECRET.search(k):
                safe_schema(v, prefix + "." + k, out, depth + 1)
    elif isinstance(value, list):
        for v in value[:100]:
            safe_schema(v, prefix + "[]", out, depth + 1)
    return out


def schema(value):
    return {k: sorted(v) for k, v in sorted(safe_schema(value).items())}


def temporal_value(value):
    result = {"raw_type": type(value).__name__}
    dt = None
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?", value):
            return {**result, "value_omitted": True}
        result["raw"] = value
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
        if dt is not None and dt.tzinfo is None:
            result["interpretation"] = "timezone_missing_do_not_assume"
            dt = None
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return {**result, "value_omitted": True}
        result["raw"] = value
        if 946684800 <= value <= 4102444800:
            dt = datetime.fromtimestamp(value, timezone.utc)
            result["unit_inference"] = "epoch_seconds_candidate"
        elif 946684800000 <= value <= 4102444800000:
            dt = datetime.fromtimestamp(value / 1000, timezone.utc)
            result["unit_inference"] = "epoch_milliseconds_candidate"
        else:
            result["interpretation"] = "not_a_supported_epoch_may_be_duration"
    elif value is None:
        result["raw"] = None
    else:
        result["value_omitted"] = True
    if dt is not None:
        result["utc_plus_8"] = dt.astimezone(CN).isoformat()
        result["local_date"] = dt.astimezone(CN).date().isoformat()
    return result


def temporal_fields(value, prefix="$"):
    out = {}
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or SECRET.search(k):
                continue
            path = prefix + "." + k
            if TEMPORAL.search(k) and not isinstance(v, (dict, list)):
                out[path] = temporal_value(v)
            if isinstance(v, (dict, list)):
                out.update(temporal_fields(v, path))
    elif isinstance(value, list):
        for v in value[:20]:
            out.update(temporal_fields(v, prefix + "[]"))
    return out


def episode_view(item):
    ep = item.get("episode", item) if isinstance(item, dict) else {}
    if not isinstance(ep, dict):
        ep = {}
    public = {k: ep[k] for k in PUBLIC_KEYS if k in ep and isinstance(ep[k], (str, int, float, bool, type(None)))
              and not (isinstance(ep[k], float) and not math.isfinite(ep[k]))}
    if isinstance(public.get("eid"), str) and re.fullmatch(r"[0-9a-f]{24}", public["eid"]):
        public["share_url"] = "https://www.xiaoyuzhoufm.com/episode/" + public["eid"]
    return {"public_episode": public, "raw_temporal_fields": temporal_fields(item), "schema": schema(item)}


def transcript_metadata(data, duration):
    if not isinstance(duration, (int, float)) or not math.isfinite(duration):
        duration = None
    result = {"schema": schema(data), "text_exported": False, "audio_checked": False}
    if not isinstance(data, list):
        return {**result, "status": "unexpected_shape"}
    rows = [r for r in data if isinstance(r, dict)]
    nonempty = [r for r in rows if isinstance(r.get("text"), str) and r["text"].strip()]
    starts = [r["startMs"] for r in nonempty if type(r.get("startMs")) in (int, float) and math.isfinite(r["startMs"])]
    ends = [r["endMs"] for r in nonempty if type(r.get("endMs")) in (int, float) and math.isfinite(r["endMs"])]
    result.update(status="available" if nonempty else "empty_text", total_rows=len(data), text_segments=len(nonempty),
                  characters=sum(len(r["text"]) for r in nonempty), timestamped_segments=len(starts),
                  first_start_ms=starts[0] if starts else None, last_start_ms=starts[-1] if starts else None,
                  last_end_ms=max(ends) if ends else None,
                  starts_monotonic=all(a <= b for a, b in zip(starts, starts[1:])),
                  episode_duration_seconds=duration,
                  completeness="timestamps_only_not_audio_verified")
    if starts and isinstance(duration, (int, float)) and duration > 0:
        result["last_start_as_fraction_of_duration"] = round(starts[-1] / (1000 * duration), 5)
        result["tail_after_last_start_seconds"] = round(duration - starts[-1] / 1000, 3)
    return result


class Client:
    def __init__(self):
        self.token = None
        self.device_id = str(uuid.uuid4())
        self.last_request = 0.0
        self.requests = 0
        self.stage = "not_started"
        self.last_http_error = None
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def clear(self):
        self.token = None
        self.device_id = None

    def request(self, method, url, payload=None, authenticated=False, login=False, cdn=False):
        parsed = urllib.parse.urlsplit(url)
        base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise Stop("unsafe_destination")
        if cdn:
            host = parsed.hostname or ""
            if method != "GET" or authenticated or not (host.endswith(".xyzcdn.net") or host.endswith(".xiaoyuzhoufm.com")):
                raise Stop("cdn_host_needs_review")
        elif (method, base) not in ALLOWED:
            raise Stop("endpoint_not_allowlisted")
        if authenticated and (not self.token or parsed.netloc != "api.xiaoyuzhoufm.com"):
            raise Stop("missing_auth_or_wrong_host")
        if self.requests >= 10:
            raise Stop("request_limit")
        delay = 2 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        headers = {"Accept": "application/json", "Accept-Encoding": "identity", "Content-Type": "application/json;charset=UTF-8"}
        if parsed.netloc == "podcaster-api.xiaoyuzhoufm.com":
            headers.update({"Origin": "https://podcaster.xiaoyuzhoufm.com", "Referer": "https://podcaster.xiaoyuzhoufm.com/", "User-Agent": "Mozilla/5.0"})
        else:
            headers.update({"User-Agent": "Xiaoyuzhou/2.99.1(android 28)"})
        if authenticated:
            headers.update({"x-jike-access-token": self.token, "x-jike-device-id": self.device_id,
                            "os": "android", "os-version": "28", "applicationid": "app.podcast.cosmos",
                            "app-version": "2.99.1", "app-buildno": "1362", "timezone": "Asia/Shanghai",
                            "local-time": datetime.now(CN).isoformat(timespec="milliseconds")})
        req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers, method=method)
        self.stage = {
            "/v1/auth/send-code": "send_sms",
            "/v1/auth/login-with-sms": "login_sms",
            "/v1/episode-played/list-history": "history",
            "/v1/playback-progress/list": "playback_progress",
            "/v1/episode/get": "episode",
            "/v1/episode-transcript/get": "transcript_metadata",
        }.get(parsed.path, "transcript_cdn" if cdn else "unknown")
        self.last_http_error = None
        self.requests += 1
        self.last_request = time.monotonic()
        try:
            with self.opener.open(req, timeout=20) as res:
                if res.status != 200:
                    raise Stop("unexpected_http_status")
                if login:
                    token = res.headers.get("x-jike-access-token")
                    # Deliberately discard refresh token, cookies, user body and all headers.
                    if not token:
                        raise Stop("login_no_access_token")
                    self.token = token
                    return {"authenticated": True}
                body = res.read(LIMIT + 1)
                if len(body) > LIMIT:
                    raise Stop("response_size_limit")
                try:
                    return json.loads(body)
                except (ValueError, UnicodeError):
                    raise Stop("non_json_response") from None
        except urllib.error.HTTPError as err:
            status = err.code
            try:
                self.last_http_error = safe_http_error(status, err.read(16385))
            except Exception:
                self.last_http_error = {"http_status": status, "body_not_parsed": "read_error"}
            finally:
                err.close()
            raise Stop("http_" + str(status)) from None
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError):
            raise Stop("network_or_tls_error") from None


def existing_account_login(client):
    # 2026-10-12 official accounts frontend now uses web-api plus CAPTCHA.
    # Do not silently switch hosts or omit/bypass the verification challenge.
    print("旧短信直连入口已暂停：当前官网使用统一账号登录和人机验证。")
    print("区号回车默认 +86 是正确的；此次不会收集手机号或发送短信。")
    print("需要先核对新的合规交互登录方式，再继续账户验证。")
    raise Stop("browser_verification_required_legacy_sms_disabled")


def run_probe(client, report):
    history = []
    cursor = None
    next_cursor = None
    report["history_pages"] = []
    try:
        for page_number in range(1, 3):
            body = {"loadMoreKey": cursor} if cursor is not None else {}
            res = client.request("POST", API + "/v1/episode-played/list-history", body, authenticated=True)
            if not isinstance(res, dict) or not isinstance(res.get("data"), list):
                raise Stop("unexpected_history_shape")
            items = res["data"]
            next_cursor = res.get("loadMoreKey")
            report["history_pages"].append({"page": page_number, "item_count": len(items),
                "schema": schema(res), "sample": [episode_view(x) for x in items[:2]],
                "cursor_present": bool(next_cursor), "cursor_type": type(next_cursor).__name__,
                "sent_previous_cursor_unchanged": cursor is not None,
                "cursor_repeated": cursor is not None and next_cursor == cursor})
            history.extend(items)
            if not items or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
    except Stop as exc:
        report["history_error"] = {"status": str(exc)}
        if client.last_http_error:
            report["history_error"]["http_error"] = client.last_http_error
    report["date_interpretation"] = "Raw time fields retained separately. pubDate/update fields are not listening dates; candidate played/listened dates require semantic verification. UI sort and isPlayed are not dates."
    report["history_items"] = [episode_view(item) for item in history[:40]]
    report["history_total_received"] = len(history)
    report["history_export_truncated"] = len(history) > 40
    report["history_more_pages_available"] = bool(next_cursor)
    history_eids = []
    for item in history[:40]:
        ep = item.get("episode", item) if isinstance(item, dict) else {}
        eid = ep.get("eid") if isinstance(ep, dict) else None
        if isinstance(eid, str) and re.fullmatch(r"[0-9a-f]{24}", eid) and eid not in history_eids:
            history_eids.append(eid)
    if history_eids:
        try:
            progress = client.request("POST", API + "/v1/playback-progress/list",
                                      {"eids": history_eids}, authenticated=True)
            rows = progress.get("data") if isinstance(progress, dict) else None
            if isinstance(rows, dict):
                rows = rows.get("data")
            if not isinstance(rows, list):
                raise Stop("unexpected_progress_shape")
            report["playback_progress"] = {
                "status": "response_received", "schema": schema(progress),
                "items": [{"eid": row["eid"], "played_at": temporal_value(row.get("playedAt")),
                           "progress_raw": row.get("progress") if type(row.get("progress")) in (int, float)
                           and math.isfinite(row["progress"]) else None}
                          for row in rows[:40] if isinstance(row, dict) and row.get("eid") in history_eids],
                "progress_unit": "unverified",
                "date_semantics": "playedAt is a candidate most-recent play time, not a complete per-play event log; compare with App history before filtering by day."
            }
        except Stop as exc:
            report["playback_progress"] = {"status": str(exc), "items": []}
            if client.last_http_error:
                report["playback_progress"]["http_error"] = client.last_http_error
    # One target is sufficient to verify transcript access; unrelated details
    # must not consume the one-shot session before this branch is tested.
    eids = [TARGET]
    report["episodes"] = []
    target = None
    for eid in eids:
        res = client.request("GET", API + "/v1/episode/get?" + urllib.parse.urlencode({"eid": eid}), authenticated=True)
        ep = res.get("data") if isinstance(res, dict) else None
        if not isinstance(ep, dict):
            raise Stop("unexpected_episode_shape")
        report["episodes"].append(episode_view(ep))
        if eid == TARGET:
            target = ep
    tr = target.get("transcript") or {}
    media = target.get("media") or {}
    media_id = target.get("transcriptMediaId") or (tr.get("mediaId") if isinstance(tr, dict) else None) or (media.get("id") if isinstance(media, dict) else None)
    if not isinstance(media_id, str) or not media_id:
        report["transcript"] = {"status": "media_id_missing"}
        return
    res = client.request("POST", API + "/v1/episode-transcript/get", {"eid": TARGET, "mediaId": media_id}, authenticated=True)
    inner = res.get("data", {}) if isinstance(res, dict) else {}
    if isinstance(inner, dict) and isinstance(inner.get("data"), dict):
        inner = inner["data"]
    url = inner.get("transcriptUrl") if isinstance(inner, dict) else None
    report["transcript"] = {"response_schema": schema(res), "url_present": isinstance(url, str) and bool(url), "target_eid": TARGET}
    if not isinstance(url, str) or not url:
        report["transcript"]["status"] = "no_url_in_response_not_proof_of_no_transcript"
        return
    host = urllib.parse.urlsplit(url).hostname
    report["transcript"]["host"] = host
    # Signed URL stays in memory; neither URL nor token is logged or persisted.
    try:
        data = client.request("GET", url, cdn=True)
        report["transcript"].update(transcript_metadata(data, target.get("duration")))
    except Stop as exc:
        report["transcript"]["status"] = str(exc)


def save_report(report, name):
    reports = BASE / "reports"
    reports.mkdir(mode=0o700, exist_ok=True)
    directory = reports / datetime.now(CN).strftime("%Y-%m-%d")
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return path


def preflight():
    result = {"mode": "tls_only_no_auth_no_http_request", "hosts": {}}
    for host in ("api.xiaoyuzhoufm.com", "podcaster-api.xiaoyuzhoufm.com"):
        try:
            with socket.create_connection((host, 443), timeout=10) as conn:
                with ssl.create_default_context().wrap_socket(conn, server_hostname=host) as tls:
                    result["hosts"][host] = {"verified_tls": True, "version": tls.version()}
        except (OSError, ssl.SSLError) as exc:
            result["hosts"][host] = {"verified_tls": False, "error": "network_or_tls_error",
                                     "error_type": type(exc).__name__, "errno": exc.errno}
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login-and-probe", action="store_true", help="Currently paused: official login requires browser verification. No SMS is sent.")
    parser.add_argument("--preflight", action="store_true", help="Verified TLS handshakes only; no phone, SMS, tokens or HTTP calls.")
    args = parser.parse_args()
    if args.preflight:
        print(json.dumps(preflight(), ensure_ascii=False, indent=2))
        return
    if not args.login_and_probe:
        parser.print_help()
        return
    report = {"mode": "one_shot_authenticated_probe", "started_at": datetime.now(CN).isoformat(),
              "authenticated": False, "phone_or_tokens_saved": False, "refresh_used": False,
              "audio_played_or_downloaded": False, "mobile_history_sync": "not_verified"}
    client = Client()
    try:
        existing_account_login(client)
        report["authenticated"] = True
        run_probe(client, report)
        report["status"] = "completed_bounded_probe"
    except Stop as exc:
        report["status"] = str(exc)
    except (KeyboardInterrupt, EOFError):
        report["status"] = "cancelled"
    except Exception:
        report["status"] = "unexpected_error_details_suppressed"
    finally:
        report["request_count"] = client.requests
        report["stage"] = client.stage
        if client.last_http_error is not None:
            report["http_error"] = client.last_http_error
        client.clear()
    name = "xyz_probe_result_" + datetime.now(CN).strftime("%Y%m%d_%H%M%S") + ".json"
    print("脱敏报告：" + str(save_report(report, name)))
    print("状态：" + report["status"])


if __name__ == "__main__":
    main()
