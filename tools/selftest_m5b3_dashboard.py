from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from admin_backend import (
    ADMIN_VERSION,
    seconds_human,
    pct,
    safe_ratio,
    verify_admin_password,
)


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
    print("P2-M5B.3 DASHBOARD REFINEMENT SELF-TEST")
    print("=" * 64)

    assert ADMIN_VERSION == "P2-M5B.3"
    print("[PASS] version")

    encoded = make_hash("CorrectHorse42")
    assert verify_admin_password("CorrectHorse42", encoded)
    assert not verify_admin_password("bad", encoded)
    print("[PASS] admin auth")

    assert seconds_human(65) == "1m 5s"
    assert pct(42) == "42.0%"
    assert round(safe_ratio(3, 8), 1) == 37.5
    print("[PASS] helpers")

    backend = (ROOT / "admin_backend.py").read_text(encoding="utf-8")
    app = (ROOT / "admin_app.py").read_text(encoding="utf-8")

    backend_tokens = [
        "p2_admin_sessions",
        "p2_admin_area_metrics",
        "p2_admin_data_quality",
        "def sessions(",
        "def areas(",
        "def data_quality(",
    ]
    for token in backend_tokens:
        assert token in backend, token
    print("[PASS] backend contract")

    app_tokens = [
        '"Sessions"',
        '"Areas"',
        '"Data Quality"',
        "run_every=\"30s\"",
        "America/Guayaquil",
        "DATA QUALITY: PASS",
        "page_open_seconds",
        "interaction_active_seconds",
    ]
    for token in app_tokens:
        assert token in app, token
    print("[PASS] dashboard contract")

    forbidden = ["pin_hash", "pin_salt", "request.remote_addr"]
    lowered = app.lower()
    for token in forbidden:
        assert token not in lowered, token
    print("[PASS] privacy guard")

    print("DECISION : P2-M5B.3 REFINEMENT PASS")


if __name__ == "__main__":
    main()
