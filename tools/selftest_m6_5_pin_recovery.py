from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from student_access import AccessValidationError, StudentAccessService, make_recovery_material, verify_recovery_code


class FakeStore:
    def __init__(self):
        self.context = {}
        self.reset_payload = None

    def register_student(self, payload):
        raise AssertionError("not used")

    def get_login_context(self, user_code):
        raise AssertionError("not used")

    def record_login_result(self, user_id, success):
        raise AssertionError("not used")

    def get_recovery_context(self, user_code):
        return dict(self.context)

    def reset_pin_material(self, payload):
        self.reset_payload = dict(payload)
        return {"ok": True, "user_id": payload["p_user_id"]}


def main():
    print("P2-M6.5 PIN RECOVERY SELF-TEST")
    print("=" * 62)

    old_code = "ABCD-EFGH-JKLM-NPQR"
    salt, digest = make_recovery_material(old_code)

    store = FakeStore()
    store.context = {
        "found": True,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "user_code": "UTN-ABC234",
        "display_name": "Mateo",
        "account_status": "active",
        "recovery_enabled": True,
        "recovery_salt": salt,
        "recovery_hash": digest,
    }
    service = StudentAccessService(store)

    failed = False
    try:
        service.reset_pin(
            user_code="UTN-ABC234",
            recovery_code="ZZZZ-ZZZZ-ZZZZ-ZZZZ",
            new_pin="482731",
            new_pin_confirmation="482731",
        )
    except AccessValidationError as exc:
        failed = exc.code == "recovery_failed"
    assert failed
    assert store.reset_payload is None
    print("[PASS] wrong recovery rejected")

    result = service.reset_pin(
        user_code="UTN-ABC234",
        recovery_code=old_code,
        new_pin="482731",
        new_pin_confirmation="482731",
    )
    assert result.user_code == "UTN-ABC234"
    assert result.recovery_code != old_code
    assert store.reset_payload is not None
    print("[PASS] correct recovery resets PIN")

    assert verify_recovery_code(
        result.recovery_code,
        store.reset_payload["p_recovery_salt"],
        store.reset_payload["p_recovery_hash"],
    )
    assert not verify_recovery_code(
        old_code,
        store.reset_payload["p_recovery_salt"],
        store.reset_payload["p_recovery_hash"],
    )
    print("[PASS] recovery rotated / old code invalidated")

    mobile = (ROOT / "mobile_backend.py").read_text(encoding="utf-8")
    access = (ROOT / "student_access.py").read_text(encoding="utf-8")

    for token in [
        "_pin_recovery_screen",
        "p2_m6_pin_recovery",
        "Restablecer PIN",
        "p2m_reset_result",
        "nuevo código de recuperación",
    ]:
        assert token.lower() in mobile.lower(), token
    print("[PASS] recovery UI contract")

    for token in [
        "p2_access_get_recovery_context",
        "p2_access_reset_pin_material",
        "def reset_pin(",
        "verify_recovery_code(",
    ]:
        assert token in access, token
    print("[PASS] recovery engine contract")

    assert "El PIN no se incluye" in mobile
    print("[PASS] safe recovery export")

    print("DECISION : P2-M6.5 PIN RECOVERY PASS")


if __name__ == "__main__":
    main()
