"""Local saved session: private files, process lock, bounded token rotation.

Protocol reference: r266-tech/xiaoyuzhou/xiaoyuzhou/client.py.
No browser credentials, account profile or raw server diagnostics are stored.
"""
import base64
import json
import math
import os
import re
import stat
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

import xyz_probe as p
from xyz_probe import protect_path

REFRESH_URL = p.API + "/app_auth_tokens.refresh"
MAX_STATE_BYTES = 65536


def checked_token(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 8192 or any(not 33 <= ord(c) <= 126 for c in value):
        raise p.Stop("invalid_session_token")
    return value


def checked_device_id(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
        return value
    except ValueError:
        raise p.Stop("invalid_session_device") from None


def link_like(path):
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def response_token(headers, name):
    value = headers.get(name)
    if value:
        return checked_token(value)
    for raw in headers.get_all("Set-Cookie", []):
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except CookieError:
            continue
        if name in cookie:
            return checked_token(cookie[name].value)
    return None


def response_pair(headers, body=None):
    body = body if isinstance(body, dict) else {}
    result = {}
    for field, header in (("access_token", "x-jike-access-token"), ("refresh_token", "x-jike-refresh-token")):
        value = response_token(headers, header) or body.get(header)
        if not value:
            raise p.Stop("session_response_missing_tokens")
        result[field] = checked_token(value)
    return result


def access_claims(token):
    # Metadata only; decoding neither verifies the JWT nor establishes lifetime.
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return {k: data[k] for k in ("iat", "exp") if k in data
                and type(data[k]) in (int, float) and math.isfinite(data[k])
                and 0 <= data[k] <= 4102444800}
    except (ValueError, TypeError, OverflowError):
        return {}




class SavedSession:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else p.BASE
        self.directory = self.root / "tmp" / "auth"
        self.path = self.directory / "session.json"
        self.lock = None
        self.credentials = None

    def __enter__(self):
        try:
            for directory in (self.root / "tmp", self.directory):
                if link_like(directory):
                    raise p.Stop("unsafe_session_path")
                directory.mkdir(mode=0o700, exist_ok=True)
                protect_path(directory, directory=True)
            lock_path = self.directory / "session.lock"
            if link_like(lock_path):
                raise p.Stop("unsafe_session_path")
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            self.lock = os.fdopen(fd, "r+b", buffering=0)
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
                raise p.Stop("unsafe_session_path")
            protect_path(lock_path)
            if os.name == "nt":
                import msvcrt
                # The permanent lock file is never unlinked, so every process locks the same inode.
                self.lock.seek(0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # simplify: hold this lock through login/refresh/reads; one process per saved session.
            return self
        except (OSError, p.Stop) as exc:
            if self.lock:
                self.lock.close()
                self.lock = None
            if isinstance(exc, p.Stop):
                raise
            raise p.Stop("session_unavailable_or_in_use") from None

    def __exit__(self, *args):
        self.credentials = None
        if self.lock:
            self.lock.close()
            self.lock = None

    def _require_lock(self):
        if self.lock is None:
            raise p.Stop("session_lock_required")
        if link_like(self.path):
            raise p.Stop("unsafe_session_path")

    def _write(self, data):
        self._require_lock()
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix="session-", suffix=".tmp", dir=self.directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                protect_path(temporary)
                json.dump(data, handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            if os.name != "nt":
                directory_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            self.credentials = data
        except (OSError, ValueError, p.Stop):
            raise p.Stop("session_save_failed") from None
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def save_login(self, pair, device_id):
        data = {"version": 1, "state": "ready", "access_token": checked_token(pair.get("access_token")),
                "refresh_token": checked_token(pair.get("refresh_token")), "device_id": checked_device_id(device_id),
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "access_claims": access_claims(pair["access_token"])}
        self._write(data)

    def load(self, client):
        self._require_lock()
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise p.Stop("unsafe_session_file")
                if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                    raise p.Stop("unsafe_session_permissions")
                if os.name == "nt":
                    protect_path(self.path)
                raw = handle.read(MAX_STATE_BYTES + 1)
            if len(raw) > MAX_STATE_BYTES:
                raise ValueError
            data = json.loads(raw)
            if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
                raise ValueError
            checked_token(data.get("access_token"))
            checked_token(data.get("refresh_token"))
            checked_device_id(data.get("device_id"))
            if data.get("state") != "ready":
                raise p.Stop("session_relogin_required" if data.get("state") == "reauth_required" else "session_refresh_outcome_unknown")
        except FileNotFoundError:
            raise p.Stop("session_missing_use_login") from None
        except (OSError, ValueError, KeyError, TypeError):
            raise p.Stop("session_file_invalid") from None
        self.credentials = data
        self.attach(client)

    def attach(self, client):
        client.token = self.credentials["access_token"]
        client.device_id = self.credentials["device_id"]
        client.saved_session = self

    def mark_rejected(self):
        self._write({**self.credentials, "state": "reauth_required"})

    def logout(self):
        self._require_lock()
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise p.Stop("session_delete_failed") from None
        self.credentials = None

    def refresh(self, client):
        self._require_lock()
        if not self.credentials or self.credentials.get("state") != "ready":
            raise p.Stop("session_refresh_outcome_unknown")
        if client.requests >= 10:
            raise p.Stop("request_limit")
        # Durable guard BEFORE the external write. A crash or failed save cannot reuse an old refresh.
        self._write({**self.credentials, "state": "refresh_pending"})
        delay = 2 - (time.monotonic() - client.last_request)
        if delay > 0:
            time.sleep(delay)
        headers = p.app_headers(device_id=self.credentials["device_id"])
        headers["x-jike-refresh-token"] = self.credentials["refresh_token"]
        request = urllib.request.Request(REFRESH_URL, method="POST", headers=headers)
        client.requests += 1
        client.refresh_requests += 1
        client.last_request = time.monotonic()
        client.stage = "token_refresh"
        try:
            with client.opener.open(request, timeout=20) as response:
                if response.status != 200:
                    raise p.Stop("refresh_unexpected_http_status")
                raw = response.read(MAX_STATE_BYTES + 1)
                if len(raw) > MAX_STATE_BYTES:
                    raise p.Stop("refresh_response_too_large")
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeError):
                    body = {}
                pair = response_pair(response.headers, body)
            try:
                self.save_login(pair, self.credentials["device_id"])
            except p.Stop:
                raise p.Stop("session_rotation_not_saved") from None
            self.attach(client)
            client.refresh_succeeded = True
        except urllib.error.HTTPError as exc:
            status = exc.code
            p.close_http_error(exc)
            if status in (401, 403):
                self.mark_rejected()
                raise p.Stop("session_relogin_required") from None
            if status == 429:
                # A rate-limit refusal did not issue a new pair; preserve the old state for a later run.
                self._write({**self.credentials, "state": "ready"})
            raise p.Stop("refresh_http_" + str(status)) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            # Keep tokens and the durable pending marker. Do not auto-login or retry an uncertain rotation.
            raise p.Stop("refresh_network_or_tls_error") from None
