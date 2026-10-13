import json
import unittest
from unittest.mock import patch
import xyz_probe as p


class SafetyTests(unittest.TestCase):
    def test_area_code_default_matches_official_form(self):
        for value in ("", "  ", "86", "+86", " 86 "):
            self.assertEqual(p.normalize_area_code(value), "+86")
        self.assertEqual(p.normalize_area_code("852"), "+852")
        with self.assertRaises(p.Stop):
            p.normalize_area_code("++86")

    def test_error_report_never_leaks_arbitrary_text(self):
        body = json.dumps({"code": 4001, "message": "PHONE_SENTINEL TOKEN_SENTINEL",
                           "mobilePhoneNumber": "PHONE_SENTINEL", "accessToken": "TOKEN_SENTINEL",
                           "debug": {"cookie": "COOKIE_SENTINEL"}}).encode()
        out = p.safe_http_error(400, body)
        self.assertEqual(out["server_code"], 4001)
        self.assertTrue(out["unrecognized_message_withheld"])
        self.assertNotIn("SENTINEL", json.dumps(out))
        self.assertEqual(p.safe_http_error(400, b"not json")["body_not_parsed"], "not_json")

    def test_error_report_known_safe_message(self):
        out = p.safe_http_error(400, json.dumps({"code": 1037, "toast": "需要验证"}).encode())
        self.assertEqual(out["server_code"], 1037)
        self.assertEqual(out["server_message"], "需要验证")
        self.assertEqual(p.safe_http_error(400, b"x" * 16385)["body_not_parsed"], "size_limit")

    def test_legacy_login_stops_before_input_or_network(self):
        client = p.Client()
        with patch("builtins.input", side_effect=AssertionError("must not prompt")), patch("builtins.print"):
            with self.assertRaisesRegex(p.Stop, "browser_verification_required_legacy_sms_disabled"):
                p.existing_account_login(client)
        self.assertEqual(client.requests, 0)

    def test_secret_values_never_exported(self):
        raw = {"accessToken": "SECRET_SENTINEL", "user": {"mobilePhoneNumber": "PHONE_SENTINEL"},
               "playedAt": "2026-10-11T16:20:00Z", "episode": {"eid": "a" * 24, "title": "fixture",
               "pubDate": "2026-10-09T00:00:00Z", "refreshToken": "REFRESH_SENTINEL"}}
        encoded = json.dumps(p.episode_view(raw))
        for secret in ("SECRET_SENTINEL", "PHONE_SENTINEL", "REFRESH_SENTINEL"):
            self.assertNotIn(secret, encoded)
        self.assertIn("$.playedAt", encoded)
        self.assertIn("$.episode.pubDate", encoded)
        self.assertEqual(p.temporal_fields(raw)["$.playedAt"]["local_date"], "2026-10-12")

    def test_no_timezone_assumption_or_duration_as_date(self):
        self.assertNotIn("utc_plus_8", p.temporal_value("2026-10-12T01:00:00"))
        self.assertNotIn("utc_plus_8", p.temporal_value(4680))

    def test_auth_host_and_mutation_blocked(self):
        client = p.Client()
        client.token = "SECRET_SENTINEL"
        for url in (p.API + "/v1/episode-played/create", "https://example.com/v1/episode/get", "http://api.xiaoyuzhoufm.com/v1/episode/get"):
            with self.assertRaises(p.Stop):
                client.request("POST", url, authenticated=True)
        self.assertEqual(client.requests, 0)

    def test_redirects_and_cdn_token_blocked(self):
        with self.assertRaises(p.Stop):
            p.NoRedirect().redirect_request(None, None, 302, None, None, "https://example.com")
        client = p.Client()
        with self.assertRaises(p.Stop):
            client.request("GET", "https://cdn.xyzcdn.net/fixture", authenticated=True, cdn=True)

    def test_transcript_metadata_does_not_export_text(self):
        metadata = p.transcript_metadata([{"text": "COPYRIGHT_SENTINEL", "startMs": 0},
                                          {"text": "PRIVATE_SENTINEL", "startMs": 4600000}], 4680)
        self.assertEqual(metadata["text_segments"], 2)
        self.assertEqual(metadata["tail_after_last_start_seconds"], 80)
        self.assertNotIn("SENTINEL", json.dumps(metadata))

    def test_two_pages_cursor_preserved_and_single_target(self):
        calls = []
        cursor = {"opaque": "CURSOR_SENTINEL", "time": 123}
        def fake(method, url, payload=None, **kwargs):
            calls.append((url, payload))
            if "list-history" in url:
                return {"data": [{"episode": {"eid": str(i) * 24}} for i in range(1, 5)], "loadMoreKey": cursor}
            if "episode/get" in url:
                return {"data": {"eid": "fixture", "duration": 4680}}
            if "playback-progress/list" in url:
                return {"data": [{"eid": "1" * 24, "playedAt": "2026-10-12T07:00:00Z",
                                  "progress": 300, "accessToken": "TOKEN_SENTINEL"}]}
            self.fail("unexpected request")
        client = p.Client()
        client.request = fake
        report = {}
        p.run_probe(client, report)
        self.assertIs(calls[1][1]["loadMoreKey"], cursor)
        self.assertEqual(sum("list-history" in url for url, _ in calls), 2)
        self.assertEqual(sum("episode/get" in url for url, _ in calls), 1)
        self.assertNotIn("CURSOR_SENTINEL", json.dumps(report))
        self.assertNotIn("TOKEN_SENTINEL", json.dumps(report))
        self.assertEqual(report["playback_progress"]["items"][0]["progress_raw"], 300)

    def test_login_requires_real_terminal_before_any_network(self):
        client = p.Client()
        with patch("sys.stdin.isatty", return_value=False), self.assertRaises(p.Stop):
            p.existing_account_login(client)
        self.assertEqual(client.requests, 0)


if __name__ == "__main__":
    unittest.main()
