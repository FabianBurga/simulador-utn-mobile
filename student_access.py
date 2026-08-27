from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
import hashlib
import hmac
import re
import secrets

ACCESS_SCHEMA_VERSION = "p2_access_v1"
ACCESS_ENGINE_VERSION = "P2-M6.3"
PIN_ITERATIONS = 220_000
RECOVERY_ITERATIONS = 220_000

STUDENT_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
WEAK_PINS = {
    "000000", "111111", "222222", "333333", "444444",
    "555555", "666666", "777777", "888888", "999999",
    "123456", "654321", "012345", "543210",
}


class AccessStore(Protocol):
    def register_student(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_login_context(self, user_code: str) -> dict[str, Any]: ...
    def record_login_result(self, user_id: str, success: bool) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RegistrationResult:
    user_id: str
    user_code: str
    display_name: str
    recovery_code: str
    cohort_id: str | None


@dataclass(frozen=True)
class LoginResult:
    ok: bool
    user_id: str | None = None
    user_code: str | None = None
    display_name: str | None = None
    account_status: str | None = None
    locked_until: str | None = None
    remaining_attempts: int | None = None
    message: str = ""


class AccessValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message


def normalize_user_code(value: str) -> str:
    raw = str(value or "").strip().upper()
    raw = re.sub(r"[^A-Z0-9-]", "", raw)
    return raw[:16]


def normalize_invite_code(value: str) -> str:
    raw = str(value or "").strip().upper()
    raw = re.sub(r"\s+", "", raw)
    return raw[:64]


def validate_display_name(value: str) -> str:
    name = " ".join(str(value or "").strip().split())
    if not 1 <= len(name) <= 60:
        raise AccessValidationError(
            "invalid_display_name",
            "Escribe un nombre o alias de 1 a 60 caracteres.",
        )
    return name


def validate_registration_pin(pin: str, confirmation: str | None = None) -> str:
    value = str(pin or "")
    if not re.fullmatch(r"\d{6}", value):
        raise AccessValidationError(
            "invalid_pin",
            "El PIN debe tener exactamente 6 números.",
        )
    if value in WEAK_PINS or len(set(value)) == 1:
        raise AccessValidationError(
            "weak_pin",
            "Elige un PIN menos predecible.",
        )
    if confirmation is not None and value != str(confirmation):
        raise AccessValidationError(
            "pin_mismatch",
            "Los dos PIN no coinciden.",
        )
    return value


def make_pin_material(pin: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(pin).encode("utf-8"),
        salt,
        PIN_ITERATIONS,
    )
    return salt.hex(), digest.hex()


def verify_pin(pin: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(str(salt_hex))
        expected = bytes.fromhex(str(hash_hex))
    except Exception:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        str(pin).encode("utf-8"),
        salt,
        PIN_ITERATIONS,
    )
    return hmac.compare_digest(candidate, expected)


def generate_student_code() -> str:
    suffix = "".join(secrets.choice(STUDENT_CODE_ALPHABET) for _ in range(6))
    return f"UTN-{suffix}"


def generate_recovery_code() -> str:
    raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def normalize_recovery_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def make_recovery_material(recovery_code: str) -> tuple[str, str]:
    normalized = normalize_recovery_code(recovery_code)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalized.encode("utf-8"),
        salt,
        RECOVERY_ITERATIONS,
    )
    return salt.hex(), digest.hex()


def verify_recovery_code(code: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(str(salt_hex))
        expected = bytes.fromhex(str(hash_hex))
    except Exception:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        normalize_recovery_code(code).encode("utf-8"),
        salt,
        RECOVERY_ITERATIONS,
    )
    return hmac.compare_digest(candidate, expected)


def hash_invite_code(invite_code: str) -> str:
    normalized = normalize_invite_code(invite_code)
    if len(normalized) < 6:
        raise AccessValidationError(
            "invalid_invite",
            "El código de acceso no es válido.",
        )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


class SupabaseAccessStore:
    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_config(cls, url: str, secret_key: str) -> "SupabaseAccessStore":
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError("Falta instalar 'supabase'.") from exc
        return cls(create_client(url, secret_key))

    def register_student(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.client.rpc("p2_access_register_student", payload).execute()
        data = result.data
        if isinstance(data, list):
            return dict(data[0]) if data else {}
        return dict(data or {})

    def get_login_context(self, user_code: str) -> dict[str, Any]:
        result = self.client.rpc(
            "p2_access_get_login_context",
            {"p_user_code": user_code},
        ).execute()
        data = result.data
        if isinstance(data, list):
            return dict(data[0]) if data else {"found": False}
        return dict(data or {"found": False})

    def record_login_result(self, user_id: str, success: bool) -> dict[str, Any]:
        result = self.client.rpc(
            "p2_access_record_login_result",
            {"p_user_id": user_id, "p_success": bool(success)},
        ).execute()
        data = result.data
        if isinstance(data, list):
            return dict(data[0]) if data else {}
        return dict(data or {})


class StudentAccessService:
    def __init__(self, store: AccessStore):
        self.store = store

    def register(
        self,
        *,
        invite_code: str,
        display_name: str,
        pin: str,
        pin_confirmation: str,
        privacy_notice_version: str = "p2_privacy_v1",
        max_code_retries: int = 8,
    ) -> RegistrationResult:
        invite_hash = hash_invite_code(invite_code)
        name = validate_display_name(display_name)
        clean_pin = validate_registration_pin(pin, pin_confirmation)
        pin_salt, pin_hash = make_pin_material(clean_pin)

        recovery_code = generate_recovery_code()
        recovery_salt, recovery_hash = make_recovery_material(recovery_code)

        last_error = "registration_failed"
        for _ in range(max_code_retries):
            user_code = generate_student_code()
            result = self.store.register_student({
                "p_invite_code_hash": invite_hash,
                "p_user_code": user_code,
                "p_display_name": name,
                "p_pin_salt": pin_salt,
                "p_pin_hash": pin_hash,
                "p_recovery_salt": recovery_salt,
                "p_recovery_hash": recovery_hash,
                "p_privacy_notice_version": privacy_notice_version,
            })
            if result.get("ok") is True:
                return RegistrationResult(
                    user_id=str(result["user_id"]),
                    user_code=str(result["user_code"]),
                    display_name=str(result.get("display_name") or name),
                    recovery_code=recovery_code,
                    cohort_id=(
                        str(result["cohort_id"])
                        if result.get("cohort_id") is not None
                        else None
                    ),
                )
            last_error = str(result.get("error") or "registration_failed")
            if last_error == "user_code_collision":
                continue
            break

        public_messages = {
            "invalid_invite": "El código de acceso no es válido.",
            "invite_unavailable": "El código de acceso no está disponible.",
            "invite_not_started": "Este código de acceso todavía no está habilitado.",
            "invite_expired": "El código de acceso ha expirado.",
            "invite_exhausted": "Este código de acceso ya alcanzó su límite.",
            "invalid_display_name": "El nombre o alias no es válido.",
        }
        raise AccessValidationError(
            last_error,
            public_messages.get(
                last_error,
                "No se pudo crear la cuenta. Intenta nuevamente.",
            ),
        )

    def login(self, *, user_code: str, pin: str) -> LoginResult:
        code = normalize_user_code(user_code)
        if not re.fullmatch(r"UTN-[A-Z0-9]{6}", code):
            return LoginResult(ok=False, message="Código o PIN incorrecto.")

        context = self.store.get_login_context(code)
        if not context.get("found"):
            return LoginResult(ok=False, message="Código o PIN incorrecto.")

        status = str(context.get("account_status") or "active")
        if status != "active":
            message = (
                "Esta cuenta está suspendida."
                if status == "suspended"
                else "Esta cuenta no está activa."
            )
            return LoginResult(
                ok=False,
                user_id=str(context.get("user_id") or "") or None,
                user_code=code,
                display_name=str(context.get("display_name") or "") or None,
                account_status=status,
                message=message,
            )

        locked_until = parse_utc(context.get("locked_until"))
        now = datetime.now(timezone.utc)
        if locked_until and locked_until > now:
            return LoginResult(
                ok=False,
                user_id=str(context.get("user_id") or "") or None,
                user_code=code,
                display_name=str(context.get("display_name") or "") or None,
                account_status=status,
                locked_until=locked_until.isoformat(),
                message="Acceso temporalmente bloqueado. Intenta más tarde.",
            )

        user_id = str(context.get("user_id") or "")
        good = verify_pin(
            pin,
            str(context.get("pin_salt") or ""),
            str(context.get("pin_hash") or ""),
        )
        security_result = self.store.record_login_result(user_id, good)

        if not good:
            if security_result.get("locked"):
                return LoginResult(
                    ok=False,
                    user_id=user_id,
                    user_code=code,
                    display_name=str(context.get("display_name") or "") or None,
                    account_status=status,
                    locked_until=str(security_result.get("locked_until") or "") or None,
                    remaining_attempts=0,
                    message="Acceso temporalmente bloqueado. Intenta en 15 minutos.",
                )
            remaining = security_result.get("remaining_attempts")
            return LoginResult(
                ok=False,
                user_id=user_id,
                user_code=code,
                display_name=str(context.get("display_name") or "") or None,
                account_status=status,
                remaining_attempts=int(remaining) if remaining is not None else None,
                message="Código o PIN incorrecto.",
            )

        return LoginResult(
            ok=True,
            user_id=user_id,
            user_code=code,
            display_name=str(context.get("display_name") or "") or code,
            account_status=status,
            message="Acceso correcto.",
        )


def recovery_credentials_text(result: RegistrationResult) -> str:
    return (
        "SIMULADOR UTN - DATOS DE ACCESO\n"
        "================================\n\n"
        f"Nombre o alias: {result.display_name}\n"
        f"Código de estudiante: {result.user_code}\n"
        f"Código de recuperación: {result.recovery_code}\n\n"
        "Guarda este documento en un lugar seguro.\n"
        "El PIN no se incluye aquí por seguridad.\n"
    )


def build_access_service_from_streamlit(st) -> StudentAccessService:
    try:
        cfg = dict(st.secrets["supabase"])
    except Exception as exc:
        raise RuntimeError("Faltan los Secrets de Supabase.") from exc
    url = str(cfg.get("url", "")).strip()
    secret_key = str(
        cfg.get("secret_key", cfg.get("service_role_key", ""))
    ).strip()
    if not url or not secret_key:
        raise RuntimeError("Configuración Supabase incompleta.")
    return StudentAccessService(
        SupabaseAccessStore.from_config(url, secret_key)
    )
