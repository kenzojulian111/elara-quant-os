import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

class _Client:
    def __init__(self, host: str):
        self.host = host


class _Req:
    def __init__(self, headers, client_host: str):
        self.headers = headers
        self.client = _Client(client_host)


def _build_request(*, forwarded_for: str = "", real_ip: str = "", client_host: str = "127.0.0.1") -> _Req:
    headers = {}
    if forwarded_for:
        headers["X-Forwarded-For"] = forwarded_for
    if real_ip:
        headers["X-Real-IP"] = real_ip
    return _Req(headers=headers, client_host=client_host)


class WebhookSecuritySmokeTests(unittest.TestCase):
    def test_smoke(self):
        self.assertTrue(True)


class WebhookClientIpExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from apps.webhook_fastapi import main as webhook_main
        except ModuleNotFoundError as e:
            raise unittest.SkipTest(f"fastapi stack unavailable in this env: {e}")
        cls.webhook_main = webhook_main

    def test_ignores_forwarded_for_when_proxy_disabled(self):
        req = _build_request(forwarded_for="8.8.8.8", client_host="10.10.10.10")
        with patch.object(self.webhook_main.Config, "WEB_REVERSE_PROXY_ENABLED", False), \
             patch.object(self.webhook_main.Config, "WEBHOOK_CLIENT_IP_MAX_LEN", 128):
            ip = self.webhook_main._extract_client_ip(req)
        self.assertEqual(ip, "10.10.10.10")

    def test_prefers_forwarded_for_when_proxy_enabled(self):
        req = _build_request(forwarded_for="8.8.8.8, 1.1.1.1", client_host="10.10.10.10")
        with patch.object(self.webhook_main.Config, "WEB_REVERSE_PROXY_ENABLED", True), \
             patch.object(self.webhook_main.Config, "WEBHOOK_CLIENT_IP_MAX_LEN", 128):
            ip = self.webhook_main._extract_client_ip(req)
        self.assertEqual(ip, "8.8.8.8")

    def test_uses_x_real_ip_when_proxy_enabled_and_forwarded_for_missing(self):
        req = _build_request(real_ip="9.9.9.9", client_host="10.10.10.10")
        with patch.object(self.webhook_main.Config, "WEB_REVERSE_PROXY_ENABLED", True), \
             patch.object(self.webhook_main.Config, "WEBHOOK_CLIENT_IP_MAX_LEN", 128):
            ip = self.webhook_main._extract_client_ip(req)
        self.assertEqual(ip, "9.9.9.9")


if __name__ == "__main__":
    unittest.main()

