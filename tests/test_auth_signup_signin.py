import os
import shutil
import tempfile
import unittest
import time
import hmac
import base64
import hashlib

from config.settings import Config
from database.database_manager import DatabaseManager
from database.models import db
from runtime.interfaces.web_factory import create_web_app


class _DummySkill:
    def __init__(self):
        self.skill_manager = None


class AuthSignupSigninTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_auth_")
        cls._db_path = os.path.join(cls._tmpdir, "auth.sqlite")
        cls._cfg_backup = {
            "SQLALCHEMY_DATABASE_URI": getattr(Config, "SQLALCHEMY_DATABASE_URI", ""),
            "SQLALCHEMY_TRACK_MODIFICATIONS": getattr(Config, "SQLALCHEMY_TRACK_MODIFICATIONS", False),
            "SECRET_KEY": getattr(Config, "SECRET_KEY", ""),
            "JWT_SECRET_KEY": getattr(Config, "JWT_SECRET_KEY", ""),
            "WEB_CSRF_PROTECT": getattr(Config, "WEB_CSRF_PROTECT", True),
            "WEB_SECURITY_HEADERS": getattr(Config, "WEB_SECURITY_HEADERS", True),
            "DB_AUTO_CREATE_TABLES": getattr(Config, "DB_AUTO_CREATE_TABLES", True),
            "JWT_REVOKE_ENABLED": getattr(Config, "JWT_REVOKE_ENABLED", True),
            "WEB_ACTIVITY_ALERT_TG_ENABLED": getattr(Config, "WEB_ACTIVITY_ALERT_TG_ENABLED", False),
            "WEB_EXPOSE_DEV_AUTH_TOKENS": getattr(Config, "WEB_EXPOSE_DEV_AUTH_TOKENS", False),
            "WEB_REQUIRE_EMAIL_VERIFICATION": getattr(Config, "WEB_REQUIRE_EMAIL_VERIFICATION", False),
            "WEB_ADMIN_MFA_REQUIRED": getattr(Config, "WEB_ADMIN_MFA_REQUIRED", True),
            "WEB_MFA_TOTP_PERIOD_SEC": getattr(Config, "WEB_MFA_TOTP_PERIOD_SEC", 30),
            "WEB_MFA_TOTP_WINDOW_STEPS": getattr(Config, "WEB_MFA_TOTP_WINDOW_STEPS", 1),
        }

        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{cls._db_path}"
        Config.SQLALCHEMY_TRACK_MODIFICATIONS = False
        Config.SECRET_KEY = "test-secret"
        Config.JWT_SECRET_KEY = "test-secret"
        Config.WEB_CSRF_PROTECT = False
        Config.WEB_SECURITY_HEADERS = False
        Config.DB_AUTO_CREATE_TABLES = True
        Config.JWT_REVOKE_ENABLED = True
        Config.WEB_ACTIVITY_ALERT_TG_ENABLED = False
        Config.WEB_EXPOSE_DEV_AUTH_TOKENS = True
        Config.WEB_REQUIRE_EMAIL_VERIFICATION = False
        Config.WEB_ADMIN_MFA_REQUIRED = True
        Config.WEB_MFA_TOTP_PERIOD_SEC = 30
        Config.WEB_MFA_TOTP_WINDOW_STEPS = 1

        cls.db_manager = DatabaseManager()
        cls.app, cls.socketio = create_web_app(_DummySkill(), cls.db_manager)
        cls.app.testing = True
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._cfg_backup.items():
            setattr(Config, key, value)
        try:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)
        except Exception:
            pass

    @staticmethod
    def _auth_header(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def setUp(self):
        self.client = self.app.test_client()
        self.socketio = self.__class__.socketio

    @staticmethod
    def _totp(secret: str, *, period: int = 30, ts: int | None = None) -> str:
        now = int(ts if ts is not None else time.time())
        counter = int(now // period)
        key = str(secret or "").strip().replace(" ", "").upper()
        padding = "=" * ((8 - (len(key) % 8)) % 8)
        decoded = base64.b32decode(key + padding, casefold=True)
        msg = counter.to_bytes(8, "big")
        digest = hmac.new(decoded, msg, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        code = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000
        return f"{code:06d}"

    def test_signin_signup_pages_and_api_flow(self):
        page_login = self.client.get("/login")
        self.assertEqual(page_login.status_code, 200)
        self.assertIn("Sign In", page_login.get_data(as_text=True))

        page_signup = self.client.get("/signup")
        self.assertEqual(page_signup.status_code, 200)
        self.assertIn("Sign Up", page_signup.get_data(as_text=True))

        bad_signup = self.client.post(
            "/api/signup",
            json={"email": "newuser@test.local", "password": "short", "name": "New User"},
        )
        self.assertEqual(bad_signup.status_code, 400)

        signup = self.client.post(
            "/api/signup",
            json={"email": "newuser@test.local", "password": "strongpass1", "name": "New User"},
        )
        self.assertEqual(signup.status_code, 201, signup.get_data(as_text=True))
        signup_payload = signup.get_json() or {}
        token = str(signup_payload.get("access_token") or "")
        self.assertTrue(token)
        self.assertEqual((signup_payload.get("user") or {}).get("role"), "user")

        me = self.client.get("/api/me", headers=self._auth_header(token))
        self.assertEqual(me.status_code, 200, me.get_data(as_text=True))
        me_payload = me.get_json() or {}
        self.assertEqual(me_payload.get("email"), "newuser@test.local")
        self.assertEqual(me_payload.get("role"), "user")

        market = self.client.get("/api/market/realtime?limit=30", headers=self._auth_header(token))
        self.assertEqual(market.status_code, 200, market.get_data(as_text=True))
        market_payload = market.get_json() or {}
        self.assertIn("stock", market_payload)
        self.assertIn("crypto", market_payload)
        self.assertIn("selection", market_payload)
        self.assertIn("points", market_payload.get("stock") or {})
        self.assertIn("points", market_payload.get("crypto") or {})
        self.assertEqual((market_payload.get("selection") or {}).get("stock_timeframe"), "1h")
        self.assertEqual((market_payload.get("selection") or {}).get("crypto_timeframe"), "1h")
        self.assertIn("timeframe", market_payload.get("stock") or {})
        self.assertIn("available_symbols", market_payload.get("stock") or {})
        self.assertIn("timeframe", market_payload.get("crypto") or {})
        self.assertIn("available_symbols", market_payload.get("crypto") or {})

        market_custom = self.client.get(
            "/api/market/realtime?limit=40&stock_symbol=AAPL&crypto_symbol=BTCUSDT&stock_timeframe=4h&crypto_timeframe=15m",
            headers=self._auth_header(token),
        )
        self.assertEqual(market_custom.status_code, 200, market_custom.get_data(as_text=True))
        market_custom_payload = market_custom.get_json() or {}
        selection = market_custom_payload.get("selection") or {}
        self.assertEqual(selection.get("stock_symbol"), "AAPL")
        self.assertEqual(selection.get("crypto_symbol"), "BTCUSDT")
        self.assertEqual(selection.get("stock_timeframe"), "4h")
        self.assertEqual(selection.get("crypto_timeframe"), "15m")
        self.assertEqual((market_custom_payload.get("stock") or {}).get("timeframe"), "4h")
        self.assertEqual((market_custom_payload.get("crypto") or {}).get("timeframe"), "15m")

        create_alert = self.client.post(
            "/api/alerts/rules",
            headers=self._auth_header(token),
            json={
                "name": "BTC breakout",
                "market_kind": "crypto",
                "symbol": "BTCUSDT",
                "condition": "cross_above",
                "target_price": 1000,
                "cooldown_sec": 300,
                "notify_channel": "portal",
                "scope": "global",
            },
        )
        self.assertEqual(create_alert.status_code, 201, create_alert.get_data(as_text=True))
        create_alert_payload = create_alert.get_json() or {}
        created_rule = create_alert_payload.get("rule") or {}
        self.assertEqual(created_rule.get("scope"), "user")
        rule_id = int(created_rule.get("id") or 0)
        self.assertGreater(rule_id, 0)

        list_alerts = self.client.get("/api/alerts/rules?limit=100", headers=self._auth_header(token))
        self.assertEqual(list_alerts.status_code, 200, list_alerts.get_data(as_text=True))
        list_alerts_payload = list_alerts.get_json() or {}
        self.assertTrue(any(int((row or {}).get("id") or 0) == rule_id for row in (list_alerts_payload.get("items") or [])))

        patch_alert = self.client.patch(
            f"/api/alerts/rules/{rule_id}",
            headers=self._auth_header(token),
            json={"is_active": False},
        )
        self.assertEqual(patch_alert.status_code, 200, patch_alert.get_data(as_text=True))
        self.assertFalse(bool(((patch_alert.get_json() or {}).get("rule") or {}).get("is_active", True)))

        events_alert = self.client.get("/api/alerts/events?limit=50", headers=self._auth_header(token))
        self.assertEqual(events_alert.status_code, 200, events_alert.get_data(as_text=True))
        self.assertIn("items", events_alert.get_json() or {})

        delete_alert = self.client.delete(f"/api/alerts/rules/{rule_id}", headers=self._auth_header(token))
        self.assertEqual(delete_alert.status_code, 200, delete_alert.get_data(as_text=True))

        sio = self.socketio.test_client(
            self.app,
            flask_test_client=self.client,
            auth={"token": token},
        )
        self.assertTrue(sio.is_connected())
        sio.get_received()
        sio.emit(
            "market_subscribe",
            {
                "stock_symbol": "AAPL",
                "crypto_symbol": "BTCUSDT",
                "stock_timeframe": "4h",
                "crypto_timeframe": "15m",
                "limit": 40,
                "refresh": 1,
            },
        )
        time.sleep(0.05)
        ws_events = [evt for evt in sio.get_received() if evt.get("name") == "market_realtime_event"]
        self.assertTrue(ws_events)
        ws_payload = ((ws_events[-1].get("args") or [{}])[0] or {})
        ws_selection = ws_payload.get("selection") or {}
        self.assertEqual(ws_selection.get("stock_symbol"), "AAPL")
        self.assertEqual(ws_selection.get("crypto_symbol"), "BTCUSDT")
        self.assertEqual(ws_selection.get("stock_timeframe"), "4h")
        self.assertEqual(ws_selection.get("crypto_timeframe"), "15m")
        self.assertEqual((ws_payload.get("stock") or {}).get("timeframe"), "4h")
        self.assertEqual((ws_payload.get("crypto") or {}).get("timeframe"), "15m")
        sio.emit("market_ping", {"seq": 77})
        time.sleep(0.05)
        pong_events = [evt for evt in sio.get_received() if evt.get("name") == "market_pong"]
        self.assertTrue(pong_events)
        pong_payload = ((pong_events[-1].get("args") or [{}])[0] or {})
        self.assertEqual(pong_payload.get("seq"), 77)
        self.assertIn("ts", pong_payload)
        sio.disconnect()

        duplicate = self.client.post(
            "/api/register",
            json={"email": "newuser@test.local", "password": "strongpass1", "name": "Another"},
        )
        self.assertEqual(duplicate.status_code, 409)

        signin = self.client.post(
            "/api/signin",
            json={"email": "newuser@test.local", "password": "strongpass1"},
        )
        self.assertEqual(signin.status_code, 200, signin.get_data(as_text=True))
        self.assertTrue(str((signin.get_json() or {}).get("access_token") or ""))

        signin_legacy = self.client.post(
            "/api/login",
            json={"email": "newuser@test.local", "password": "strongpass1"},
        )
        self.assertEqual(signin_legacy.status_code, 200, signin_legacy.get_data(as_text=True))

    def test_password_reset_and_email_verify_endpoints(self):
        signup = self.client.post(
            "/api/signup",
            json={"email": "resetme@test.local", "password": "firstpass123", "name": "Reset Me"},
        )
        self.assertEqual(signup.status_code, 201, signup.get_data(as_text=True))

        verify_req = self.client.post(
            "/api/email/verify/request",
            json={"email": "resetme@test.local"},
        )
        self.assertEqual(verify_req.status_code, 200, verify_req.get_data(as_text=True))
        verify_token = str((verify_req.get_json() or {}).get("verification_token") or "")
        self.assertTrue(verify_token)

        verify_confirm = self.client.post(
            "/api/email/verify/confirm",
            json={"token": verify_token},
        )
        self.assertEqual(verify_confirm.status_code, 200, verify_confirm.get_data(as_text=True))

        forgot = self.client.post(
            "/api/password/forgot",
            json={"email": "resetme@test.local"},
        )
        self.assertEqual(forgot.status_code, 200, forgot.get_data(as_text=True))
        reset_token = str((forgot.get_json() or {}).get("reset_token") or "")
        self.assertTrue(reset_token)

        reset = self.client.post(
            "/api/password/reset",
            json={"token": reset_token, "new_password": "secondpass456"},
        )
        self.assertEqual(reset.status_code, 200, reset.get_data(as_text=True))

        old_signin = self.client.post(
            "/api/signin",
            json={"email": "resetme@test.local", "password": "firstpass123"},
        )
        self.assertEqual(old_signin.status_code, 401, old_signin.get_data(as_text=True))

        new_signin = self.client.post(
            "/api/signin",
            json={"email": "resetme@test.local", "password": "secondpass456"},
        )
        self.assertEqual(new_signin.status_code, 200, new_signin.get_data(as_text=True))

    def test_account_profile_password_and_admin_mfa_flow(self):
        signup = self.client.post(
            "/api/signup",
            json={"email": "profile@test.local", "password": "profilepass1", "name": "Profile User"},
        )
        self.assertEqual(signup.status_code, 201, signup.get_data(as_text=True))
        user_token = str((signup.get_json() or {}).get("access_token") or "")
        self.assertTrue(user_token)

        prof_get = self.client.get("/api/account/profile", headers=self._auth_header(user_token))
        self.assertEqual(prof_get.status_code, 200, prof_get.get_data(as_text=True))
        self.assertEqual((prof_get.get_json() or {}).get("email"), "profile@test.local")

        prof_patch = self.client.patch(
            "/api/account/profile",
            headers=self._auth_header(user_token),
            json={"name": "Updated Name"},
        )
        self.assertEqual(prof_patch.status_code, 200, prof_patch.get_data(as_text=True))

        pw_patch = self.client.post(
            "/api/account/password",
            headers=self._auth_header(user_token),
            json={"current_password": "profilepass1", "new_password": "profilepass2"},
        )
        self.assertEqual(pw_patch.status_code, 200, pw_patch.get_data(as_text=True))

        old_signin = self.client.post(
            "/api/signin",
            json={"email": "profile@test.local", "password": "profilepass1"},
        )
        self.assertEqual(old_signin.status_code, 401, old_signin.get_data(as_text=True))

        self.db_manager.create_user(
            email="adminmfa@test.local",
            password="adminpass1",
            name="Admin MFA",
            role="admin",
        )
        admin_login = self.client.post(
            "/api/signin",
            json={"email": "adminmfa@test.local", "password": "adminpass1"},
        )
        self.assertEqual(admin_login.status_code, 200, admin_login.get_data(as_text=True))
        admin_token = str((admin_login.get_json() or {}).get("access_token") or "")
        self.assertTrue(admin_token)

        mfa_setup = self.client.post(
            "/api/account/mfa/setup",
            headers=self._auth_header(admin_token),
        )
        self.assertEqual(mfa_setup.status_code, 200, mfa_setup.get_data(as_text=True))
        secret = str((mfa_setup.get_json() or {}).get("secret") or "")
        self.assertTrue(secret)

        otp = self._totp(secret, period=int(getattr(Config, "WEB_MFA_TOTP_PERIOD_SEC", 30)))
        mfa_enable = self.client.post(
            "/api/account/mfa/enable",
            headers=self._auth_header(admin_token),
            json={"otp": otp},
        )
        self.assertEqual(mfa_enable.status_code, 200, mfa_enable.get_data(as_text=True))

        chall = self.client.post(
            "/api/signin",
            json={"email": "adminmfa@test.local", "password": "adminpass1"},
        )
        self.assertEqual(chall.status_code, 200, chall.get_data(as_text=True))
        self.assertTrue(bool((chall.get_json() or {}).get("mfa_required")))

        otp2 = self._totp(secret, period=int(getattr(Config, "WEB_MFA_TOTP_PERIOD_SEC", 30)))
        admin_login_otp = self.client.post(
            "/api/signin",
            json={"email": "adminmfa@test.local", "password": "adminpass1", "otp": otp2},
        )
        self.assertEqual(admin_login_otp.status_code, 200, admin_login_otp.get_data(as_text=True))
        self.assertTrue(str((admin_login_otp.get_json() or {}).get("access_token") or ""))


if __name__ == "__main__":
    unittest.main()

