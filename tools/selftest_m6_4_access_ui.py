from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from student_access import (
    StudentAccessService,
    make_pin_material,
)


class FakeStore:
    def __init__(self, context):
        self.context = context
        self.results = []

    def get_login_context(self, user_code):
        return dict(self.context)

    def record_login_result(self, user_id, success):
        self.results.append((user_id, success))
        return {
            "ok": True,
            "locked": False,
            "remaining_attempts": 4,
        }

    def register_student(self, payload):
        raise AssertionError("not used")


def main():
    print("P2-M6.4 REGISTRATION + LOGIN UI SELF-TEST")
    print("=" * 66)

    mobile = (ROOT / "mobile_backend.py").read_text(encoding="utf-8")
    access = (ROOT / "student_access.py").read_text(encoding="utf-8")

    required_mobile = [
        '["Iniciar sesión", "Crear cuenta"]',
        "p2_m6_login",
        "p2_m6_register",
        "recovery_credentials_text",
        "p2m_registration_result",
        "_legacy_local_login_screen",
        "_finish_cloud_auth",
        "build_access_service_from_streamlit",
    ]
    for token in required_mobile:
        assert token in mobile, token
    print("[PASS] registration/login UI contract")

    cloud_login = re.search(
        r"def _login_screen.*?def _build_context",
        mobile,
        flags=re.S,
    )
    assert cloud_login is not None
    assert "Entrar / Crear cuenta" not in cloud_login.group(0)
    print("[PASS] cloud auto-registration removed")

    required_access = [
        're.fullmatch(r"[A-Z0-9][A-Z0-9_-]{3,23}", code)',
        're.sub(r"[^A-Z0-9_-]", "", raw)',
    ]
    for token in required_access:
        assert token in access, token
    print("[PASS] legacy cloud login compatibility")

    salt, digest = make_pin_material("482731")
    context = {
        "found": True,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "user_code": "PRUEBA01",
        "pin_salt": salt,
        "pin_hash": digest,
        "display_name": "PRUEBA01",
        "account_status": "active",
        "locked_until": None,
    }
    store = FakeStore(context)
    service = StudentAccessService(store)
    result = service.login(user_code="PRUEBA01", pin="482731")
    assert result.ok is True
    assert result.user_code == "PRUEBA01"
    print("[PASS] existing users remain able to login")

    assert "recovery_credentials_text" in mobile
    assert "st.download_button" in mobile
    assert "El PIN no se incluye" in access
    print("[PASS] one-time recovery handoff")

    print("DECISION : P2-M6.4 UI PASS")


if __name__ == "__main__":
    main()
