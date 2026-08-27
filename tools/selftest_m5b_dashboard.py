from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from admin_backend import (
    seconds_human,
    pct,
    safe_ratio,
    verify_admin_password,
)
import hashlib


def make_hash(password: str) -> str:
    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    iterations = 1000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def main():
    print("P2-M5B ADMIN SELF-TEST")
    print("=" * 58)

    encoded = make_hash("CorrectHorse42")
    assert verify_admin_password("CorrectHorse42", encoded)
    assert not verify_admin_password("bad", encoded)
    print("[PASS] admin auth")

    assert seconds_human(0) == "0s"
    assert seconds_human(65) == "1m 5s"
    assert seconds_human(3665) == "1h 1m"
    print("[PASS] duration formatting")

    assert pct(42) == "42.0%"
    assert pct(None) == "—"
    assert round(safe_ratio(2, 5), 1) == 40.0
    assert safe_ratio(2, 0) is None
    print("[PASS] metric helpers")

    source = open("admin_app.py", encoding="utf-8").read()
    required = [
        "Overview",
        "Live Now",
        "Students",
        "Attempts",
        "Questions",
        "Funnel / Retention",
        "System Health",
        "p2_admin_authenticated",
        "password_hash",
        "secret_key",
    ]
    for token in required:
        assert token in source, token
    print("[PASS] dashboard contract")

    forbidden = ["pin_hash", "pin_salt", "request.remote_addr"]
    lowered = source.lower()
    for token in forbidden:
        assert token not in lowered, token
    print("[PASS] privacy guard")

    print("DECISION : P2-M5B.1 ADMIN PASS")


if __name__ == "__main__":
    main()
