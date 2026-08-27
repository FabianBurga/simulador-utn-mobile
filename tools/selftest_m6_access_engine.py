from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from student_access import (
    AccessValidationError,
    StudentAccessService,
    generate_recovery_code,
    hash_invite_code,
    make_pin_material,
    normalize_recovery_code,
    recovery_credentials_text,
    validate_registration_pin,
    verify_pin,
)


class FakeStore:
    def __init__(self):
        self.registration_calls = []
        self.login_context = {}
        self.login_results = []
        self.collision_once = False

    def register_student(self, payload):
        self.registration_calls.append(payload)
        if self.collision_once:
            self.collision_once = False
            return {"ok": False, "error": "user_code_collision"}
        return {
            "ok": True,
            "user_id": "11111111-1111-1111-1111-111111111111",
            "user_code": payload["p_user_code"],
            "display_name": payload["p_display_name"],
            "cohort_id": None,
        }

    def get_login_context(self, user_code):
        return dict(self.login_context)

    def record_login_result(self, user_id, success):
        self.login_results.append((user_id, success))
        if success:
            return {"ok": True, "locked": False, "failed_login_count": 0}
        return {"ok": True, "locked": False, "remaining_attempts": 4}


def main():
    print("P2-M6.3 STUDENT ACCESS ENGINE SELF-TEST")
    print("=" * 62)

    salt, digest = make_pin_material("482731")
    assert verify_pin("482731", salt, digest)
    assert not verify_pin("482732", salt, digest)
    print("[PASS] P2 PIN compatibility")

    assert validate_registration_pin("482731", "482731") == "482731"
    weak_failed = False
    try:
        validate_registration_pin("123456", "123456")
    except AccessValidationError:
        weak_failed = True
    assert weak_failed
    print("[PASS] registration PIN policy")

    r = generate_recovery_code()
    assert len(normalize_recovery_code(r)) == 16
    assert r.count("-") == 3
    print("[PASS] recovery code entropy shape")

    h1 = hash_invite_code(" preutn-2026-ABCD1234 ")
    h2 = hash_invite_code("PREUTN-2026-ABCD1234")
    assert h1 == h2 and len(h1) == 64
    print("[PASS] invite normalization/hash")

    store = FakeStore()
    store.collision_once = True
    service = StudentAccessService(store)
    reg = service.register(
        invite_code="PREUTN-2026-ABCD1234",
        display_name="  Mateo   ",
        pin="482731",
        pin_confirmation="482731",
    )
    assert reg.user_code.startswith("UTN-")
    assert len(store.registration_calls) == 2
    assert store.registration_calls[0]["p_pin_hash"] == store.registration_calls[1]["p_pin_hash"]
    print("[PASS] atomic registration payload + collision retry")

    login_salt, login_hash = make_pin_material("482731")
    store.login_context = {
        "found": True,
        "user_id": reg.user_id,
        "user_code": reg.user_code,
        "pin_salt": login_salt,
        "pin_hash": login_hash,
        "display_name": "Mateo",
        "account_status": "active",
        "locked_until": None,
    }
    good = service.login(user_code=reg.user_code, pin="482731")
    bad = service.login(user_code=reg.user_code, pin="482732")
    assert good.ok is True
    assert bad.ok is False
    assert store.login_results[-2:] == [(reg.user_id, True), (reg.user_id, False)]
    print("[PASS] login + failed-attempt bridge")

    text = recovery_credentials_text(reg)
    assert reg.user_code in text
    assert reg.recovery_code in text
    assert "482731" not in text
    print("[PASS] safe credential export")

    source = (ROOT / "student_access.py").read_text(encoding="utf-8").lower()
    assert "pin_iterations = 220_000" in source
    assert "p2_access_register_student" in source
    assert "p2_access_get_login_context" in source
    assert "p2_access_record_login_result" in source
    assert "secret_key" in source
    assert "raw pin" not in source
    print("[PASS] engine contract")

    print("DECISION : P2-M6.3 ACCESS ENGINE PASS")


if __name__ == "__main__":
    main()
