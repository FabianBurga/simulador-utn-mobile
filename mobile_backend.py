from __future__ import annotations

from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid

TABLE_NAME = "p2_mobile_users"
PIN_ITERATIONS = 220_000


def _json_fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def _file_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _normalize_code(value: str) -> str:
    value = str(value or "").strip().upper()
    value = re.sub(r"[^A-Z0-9_-]", "", value)
    return value[:24]


def _valid_code(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{3,23}", value))


def _valid_pin(value: str) -> bool:
    value = str(value or "")
    return 4 <= len(value) <= 12 and value.isdigit()


def _make_pin(pin: str):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt,
        PIN_ITERATIONS,
    )
    return salt.hex(), digest.hex()


def _verify_pin(pin: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except Exception:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt,
        PIN_ITERATIONS,
    )
    return hmac.compare_digest(candidate, expected)


class LocalUserStore:
    """Backend para smoke local. No usar como base publica definitiva."""

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)

    def _load(self):
        data = _file_json(self.path, [])
        return data if isinstance(data, list) else []

    def _save(self, rows):
        _atomic_json(self.path, rows)

    def find_by_code(self, code: str):
        for row in self._load():
            if row.get("user_code") == code:
                return dict(row)
        return None

    def fetch_by_id(self, user_id: str):
        for row in self._load():
            if row.get("id") == user_id:
                return dict(row)
        return None

    def create_user(self, code: str, pin: str):
        if self.find_by_code(code):
            raise ValueError("user_exists")
        salt, pin_hash = _make_pin(pin)
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": str(uuid.uuid4()),
            "user_code": code,
            "pin_salt": salt,
            "pin_hash": pin_hash,
            "history_json": [],
            "state_json": None,
            "created_at": now,
            "updated_at": now,
        }
        rows = self._load()
        rows.append(row)
        self._save(rows)
        return dict(row)

    def update_payload(self, user_id: str, history, state):
        rows = self._load()
        found = False
        for row in rows:
            if row.get("id") == user_id:
                row["history_json"] = history
                row["state_json"] = state
                row["updated_at"] = datetime.now(timezone.utc).isoformat()
                found = True
                break
        if not found:
            raise ValueError("user_not_found")
        self._save(rows)


class SupabaseUserStore:
    """Backend cloud. Debe inicializarse con una Secret Key del servidor."""

    def __init__(self, url: str, secret_key: str):
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError("Falta instalar el paquete 'supabase'.") from exc
        self.client = create_client(url, secret_key)

    def find_by_code(self, code: str):
        response = (
            self.client.table(TABLE_NAME)
            .select("*")
            .eq("user_code", code)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return dict(rows[0]) if rows else None

    def fetch_by_id(self, user_id: str):
        response = (
            self.client.table(TABLE_NAME)
            .select("*")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return dict(rows[0]) if rows else None

    def create_user(self, code: str, pin: str):
        salt, pin_hash = _make_pin(pin)
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": str(uuid.uuid4()),
            "user_code": code,
            "pin_salt": salt,
            "pin_hash": pin_hash,
            "history_json": [],
            "state_json": None,
            "created_at": now,
            "updated_at": now,
        }
        response = self.client.table(TABLE_NAME).insert(row).execute()
        rows = response.data or []
        return dict(rows[0]) if rows else row

    def update_payload(self, user_id: str, history, state):
        payload = {
            "history_json": history,
            "state_json": state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        (
            self.client.table(TABLE_NAME)
            .update(payload)
            .eq("id", user_id)
            .execute()
        )


def _secret_section(st, section: str):
    try:
        if section in st.secrets:
            return dict(st.secrets[section])
    except Exception:
        pass
    return {}


def _build_store(st, base: Path):
    local_requested = os.environ.get("P2_MOBILE_LOCAL", "").strip() == "1"
    mobile_cfg = _secret_section(st, "mobile")
    if str(mobile_cfg.get("backend", "")).lower() == "local":
        local_requested = True

    if local_requested:
        return (
            LocalUserStore(base / "results" / "mobile_local_users.json"),
            "local",
        )

    supabase_cfg = _secret_section(st, "supabase")
    url = str(supabase_cfg.get("url", "")).strip()
    secret_key = str(
        supabase_cfg.get(
            "secret_key",
            supabase_cfg.get("service_role_key", ""),
        )
    ).strip()
    if not url or not secret_key:
        raise RuntimeError(
            "P2 Mobile no esta configurado: faltan los Secrets de Supabase."
        )
    return SupabaseUserStore(url, secret_key), "supabase"


def apply_mobile_css(st):
    st.markdown(
        """
        <style>
        @media (max-width: 768px) {
          [data-testid="stMainBlockContainer"] {
            padding-left: 0.70rem;
            padding-right: 0.70rem;
            padding-top: 0.80rem;
            padding-bottom: 5rem;
          }
          div[data-testid="stHorizontalBlock"] { gap: 0.45rem; }
          .stButton > button,
          .stDownloadButton > button,
          div[data-testid="stFormSubmitButton"] > button {
            min-height: 3rem;
            width: 100%;
            font-size: 1rem;
          }
          div[role="radiogroup"] label {
            padding-top: 0.30rem;
            padding-bottom: 0.30rem;
          }
          h1 { font-size: 1.70rem !important; }
          h2 { font-size: 1.35rem !important; }
          h3 { font-size: 1.12rem !important; }
          [data-testid="stMetricValue"] { font-size: 1.35rem; }
          [data-testid="stDataFrame"] {
            max-width: 100%;
            overflow-x: auto;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _clear_auth_state(st):
    keys = [
        key for key in list(st.session_state.keys())
        if str(key).startswith("p2m_")
    ]
    for key in keys:
        st.session_state.pop(key, None)


def _finish_cloud_auth(st, *, user_id: str, user_code: str, display_name: str, backend_name: str):
    st.session_state["p2m_authenticated"] = True
    st.session_state["p2m_user_id"] = str(user_id)
    st.session_state["p2m_user_code"] = str(user_code)
    st.session_state["p2m_display_name"] = str(display_name or user_code)
    st.session_state["p2m_backend"] = backend_name
    st.session_state["p2m_session_id"] = str(uuid.uuid4())
    st.session_state["p2m_initialized"] = False
    st.session_state["p2m_login_failures"] = 0


def _legacy_local_login_screen(st, store, backend_name: str):
    st.title("📱 Simulador UTN")
    st.caption("Modo local de prueba")
    st.warning(
        "Este modo permite crear usuarios locales solo para pruebas. "
        "Producción usa registro por invitación."
    )

    with st.form("p2_mobile_local_login", clear_on_submit=False):
        raw_code = st.text_input(
            "Código local",
            placeholder="TESTA",
            max_chars=24,
        )
        pin = st.text_input(
            "PIN local",
            type="password",
            max_chars=12,
        )
        submitted = st.form_submit_button(
            "Entrar / Crear usuario local",
            use_container_width=True,
        )

    if not submitted:
        return None

    code = _normalize_code(raw_code)
    if not _valid_code(code) or not _valid_pin(pin):
        st.error("Código o PIN local no válido.")
        return None

    try:
        row = store.find_by_code(code)
        if row is None:
            row = store.create_user(code, pin)
        elif not _verify_pin(
            pin,
            str(row.get("pin_salt", "")),
            str(row.get("pin_hash", "")),
        ):
            st.error("Código o PIN incorrecto.")
            return None

        _finish_cloud_auth(
            st,
            user_id=str(row["id"]),
            user_code=code,
            display_name=code,
            backend_name=backend_name,
        )
        st.rerun()
    except Exception:
        st.error("No se pudo iniciar la sesión local.")
    return None


def _registration_success_screen(st, service, backend_name: str):
    from student_access import RegistrationResult, recovery_credentials_text

    data = st.session_state.get("p2m_registration_result")
    if not isinstance(data, dict):
        return False

    result = RegistrationResult(
        user_id=str(data["user_id"]),
        user_code=str(data["user_code"]),
        display_name=str(data["display_name"]),
        recovery_code=str(data["recovery_code"]),
        cohort_id=data.get("cohort_id"),
    )

    st.title("✅ Cuenta creada")
    st.success("Tu cuenta está lista. Guarda tus datos antes de continuar.")

    c1, c2 = st.columns(2)
    c1.metric("Código de estudiante", result.user_code)
    c2.metric("Nombre / alias", result.display_name)

    st.warning(
        "Tu código de recuperación se muestra una sola vez. "
        "Guárdalo; servirá para recuperar tu acceso si olvidas el PIN."
    )
    st.code(result.recovery_code, language=None)

    st.download_button(
        "Descargar mis datos de acceso",
        recovery_credentials_text(result).encode("utf-8"),
        file_name=f"{result.user_code}_acceso.txt",
        mime="text/plain",
        use_container_width=True,
    )

    saved = st.checkbox(
        "Confirmo que guardé mi código de estudiante y mi código de recuperación.",
        key="p2m_registration_saved",
    )

    if st.button(
        "Entrar al simulador",
        use_container_width=True,
        disabled=not saved,
        type="primary",
    ):
        try:
            service.store.record_login_result(result.user_id, True)
        except Exception:
            pass
        st.session_state.pop("p2m_registration_result", None)
        st.session_state.pop("p2m_registration_saved", None)
        _finish_cloud_auth(
            st,
            user_id=result.user_id,
            user_code=result.user_code,
            display_name=result.display_name,
            backend_name=backend_name,
        )
        st.rerun()

    if st.button("Volver al inicio", use_container_width=True):
        st.session_state.pop("p2m_registration_result", None)
        st.session_state.pop("p2m_registration_saved", None)
        st.rerun()

    return True


def _pin_recovery_screen(st, service):
    from student_access import AccessValidationError, PinResetResult

    st.title("🔑 Recuperar acceso")
    st.caption("Usa el código de recuperación que guardaste al crear tu cuenta.")

    reset_data = st.session_state.get("p2m_reset_result")
    if isinstance(reset_data, dict):
        result = PinResetResult(
            user_id=str(reset_data["user_id"]),
            user_code=str(reset_data["user_code"]),
            display_name=str(reset_data["display_name"]),
            recovery_code=str(reset_data["recovery_code"]),
        )

        st.success("PIN actualizado correctamente.")
        st.warning(
            "Tu código de recuperación anterior ya no funciona. "
            "Guarda el nuevo código antes de volver al inicio."
        )
        st.code(result.recovery_code, language=None)

        recovery_text = (
            "SIMULADOR UTN - RECUPERACIÓN ACTUALIZADA\n"
            "========================================\n\n"
            f"Código de estudiante: {result.user_code}\n"
            f"Nuevo código de recuperación: {result.recovery_code}\n\n"
            "El código de recuperación anterior quedó invalidado.\n"
            "El PIN no se incluye en este documento.\n"
        )
        st.download_button(
            "Descargar nuevo código de recuperación",
            recovery_text.encode("utf-8"),
            file_name=f"{result.user_code}_recuperacion.txt",
            mime="text/plain",
            use_container_width=True,
        )

        saved = st.checkbox(
            "Confirmo que guardé mi nuevo código de recuperación.",
            key="p2m_reset_saved",
        )
        if st.button(
            "Volver a iniciar sesión",
            use_container_width=True,
            disabled=not saved,
            type="primary",
        ):
            st.session_state.pop("p2m_reset_result", None)
            st.session_state.pop("p2m_reset_saved", None)
            st.session_state.pop("p2m_show_recovery", None)
            st.session_state["p2m_access_mode"] = "Iniciar sesión"
            st.rerun()
        return None

    with st.form("p2_m6_pin_recovery", clear_on_submit=False):
        user_code = st.text_input(
            "Código de estudiante",
            placeholder="UTN-ABC234",
            max_chars=24,
        )
        recovery_code = st.text_input(
            "Código de recuperación",
            placeholder="XXXX-XXXX-XXXX-XXXX",
            max_chars=32,
        )
        new_pin = st.text_input(
            "Nuevo PIN de 6 números",
            type="password",
            max_chars=6,
        )
        new_pin_confirmation = st.text_input(
            "Repite el nuevo PIN",
            type="password",
            max_chars=6,
        )
        submitted = st.form_submit_button(
            "Restablecer PIN",
            use_container_width=True,
            type="primary",
        )

    if submitted:
        try:
            result = service.reset_pin(
                user_code=user_code,
                recovery_code=recovery_code,
                new_pin=new_pin,
                new_pin_confirmation=new_pin_confirmation,
            )
        except AccessValidationError as exc:
            st.error(exc.public_message)
            return None
        except Exception:
            st.error("No se pudo restablecer el PIN. Intenta nuevamente.")
            return None

        st.session_state["p2m_reset_result"] = {
            "user_id": result.user_id,
            "user_code": result.user_code,
            "display_name": result.display_name,
            "recovery_code": result.recovery_code,
        }
        st.rerun()

    if st.button("← Volver al inicio de sesión", use_container_width=True):
        st.session_state.pop("p2m_show_recovery", None)
        st.session_state["p2m_access_mode"] = "Iniciar sesión"
        st.rerun()

    return None


def _login_screen(st, store, backend_name: str):
    if backend_name == "local":
        return _legacy_local_login_screen(st, store, backend_name)

    from student_access import (
        AccessValidationError,
        build_access_service_from_streamlit,
    )

    try:
        service = build_access_service_from_streamlit(st)
    except Exception:
        st.error("No se pudo inicializar el sistema de acceso.")
        return None

    if _registration_success_screen(st, service, backend_name):
        return None

    if st.session_state.get("p2m_show_recovery", False):
        return _pin_recovery_screen(st, service)

    st.title("🎓 Simulador UTN")
    st.caption("Tu progreso queda guardado de forma independiente.")

    action = st.radio(
        "Acceso",
        ["Iniciar sesión", "Crear cuenta"],
        horizontal=True,
        label_visibility="collapsed",
        key="p2m_access_mode",
    )

    if action == "Iniciar sesión":
        st.subheader("Iniciar sesión")
        st.caption(
            "Usa tu código de estudiante y PIN. "
            "Las nuevas cuentas tienen un código como UTN-ABC234."
        )

        with st.form("p2_m6_login", clear_on_submit=False):
            raw_code = st.text_input(
                "Código de estudiante",
                placeholder="UTN-ABC234",
                max_chars=24,
            )
            pin = st.text_input(
                "PIN",
                type="password",
                max_chars=12,
                help="Las nuevas cuentas usan un PIN numérico de 6 dígitos.",
            )
            submitted = st.form_submit_button(
                "Ingresar",
                use_container_width=True,
                type="primary",
            )

        if submitted:
            try:
                result = service.login(
                    user_code=raw_code,
                    pin=pin,
                )
            except Exception:
                st.error("No se pudo validar el acceso. Intenta nuevamente.")
                return None

            if not result.ok:
                st.error(result.message or "Código o PIN incorrecto.")
                if result.remaining_attempts is not None and result.remaining_attempts > 0:
                    st.caption(
                        f"Intentos restantes antes del bloqueo temporal: "
                        f"{result.remaining_attempts}"
                    )
                return None

            _finish_cloud_auth(
                st,
                user_id=str(result.user_id),
                user_code=str(result.user_code),
                display_name=str(result.display_name or result.user_code),
                backend_name=backend_name,
            )
            st.rerun()

        if st.button("¿Olvidaste tu PIN?", use_container_width=True):
            st.session_state["p2m_show_recovery"] = True
            st.rerun()
        return None

    st.subheader("Crear cuenta")
    st.caption(
        "Necesitas un código de acceso entregado por el responsable del simulador."
    )

    with st.expander("¿Qué datos se guardan?"):
        st.markdown(
            """
            Se guarda tu nombre o alias, un código de estudiante, tu progreso académico
            y eventos básicos de uso del simulador. No necesitas entregar correo,
            teléfono, cédula, GPS ni ubicación precisa.
            """
        )

    with st.form("p2_m6_register", clear_on_submit=False):
        invite_code = st.text_input(
            "Código de acceso",
            placeholder="PREUTN-2026-...",
            max_chars=64,
        )
        display_name = st.text_input(
            "Nombre o alias",
            placeholder="Ejemplo: Mateo",
            max_chars=60,
        )
        pin = st.text_input(
            "Crea un PIN de 6 números",
            type="password",
            max_chars=6,
        )
        pin_confirmation = st.text_input(
            "Repite el PIN",
            type="password",
            max_chars=6,
        )
        privacy_ok = st.checkbox(
            "Acepto el registro de mi progreso académico y uso del simulador "
            "para personalizar y mejorar la experiencia.",
        )
        submitted = st.form_submit_button(
            "Crear cuenta",
            use_container_width=True,
            type="primary",
        )

    if not submitted:
        return None

    if not privacy_ok:
        st.error("Debes aceptar el aviso de privacidad para crear la cuenta.")
        return None

    try:
        result = service.register(
            invite_code=invite_code,
            display_name=display_name,
            pin=pin,
            pin_confirmation=pin_confirmation,
            privacy_notice_version="p2_privacy_v1",
        )
    except AccessValidationError as exc:
        st.error(exc.public_message)
        return None
    except Exception:
        st.error("No se pudo crear la cuenta. Intenta nuevamente.")
        return None

    st.session_state["p2m_registration_result"] = {
        "user_id": result.user_id,
        "user_code": result.user_code,
        "display_name": result.display_name,
        "recovery_code": result.recovery_code,
        "cohort_id": result.cohort_id,
    }
    st.rerun()
    return None


def _build_context(st, base: Path, store, backend_name: str):
    user_id = str(st.session_state["p2m_user_id"])
    session_id = str(st.session_state["p2m_session_id"])
    user_root = base / ".mobile_cache" / user_id / session_id
    results = user_root / "results"
    results.mkdir(parents=True, exist_ok=True)
    return {
        "user_id": user_id,
        "user_code": str(st.session_state["p2m_user_code"]),
        "backend": backend_name,
        "store": store,
        "user_root": str(user_root),
        "history_path": str(results / "history.json"),
        "state_path": str(results / "student_state.json"),
    }


def _initialize_cache(st, ctx):
    row = ctx["store"].fetch_by_id(ctx["user_id"])
    if row is None:
        raise RuntimeError("La cuenta ya no existe.")

    history = row.get("history_json", [])
    if not isinstance(history, list):
        history = []
    state = row.get("state_json")

    history_path = Path(ctx["history_path"])
    state_path = Path(ctx["state_path"])
    _atomic_json(history_path, history)
    if isinstance(state, dict):
        _atomic_json(state_path, state)
    elif state_path.exists():
        state_path.unlink()

    st.session_state["p2m_history_fp"] = _json_fingerprint(history)
    st.session_state["p2m_state_fp"] = _json_fingerprint(state)
    st.session_state["p2m_initialized"] = True


def mobile_push_user_files(st, ctx, force: bool = False):
    history_path = Path(ctx["history_path"])
    state_path = Path(ctx["state_path"])
    history = _file_json(history_path, [])
    if not isinstance(history, list):
        history = []
    state = _file_json(state_path, None)
    if not isinstance(state, dict):
        state = None

    history_fp = _json_fingerprint(history)
    state_fp = _json_fingerprint(state)
    history_changed = history_fp != st.session_state.get("p2m_history_fp")
    state_changed = state_fp != st.session_state.get("p2m_state_fp")

    if force or history_changed or state_changed:
        ctx["store"].update_payload(ctx["user_id"], history, state)
        st.session_state["p2m_history_fp"] = history_fp
        st.session_state["p2m_state_fp"] = state_fp
        return True
    return False


def mobile_bootstrap(st, base: str | Path):
    base = Path(base)
    apply_mobile_css(st)

    try:
        store, backend_name = _build_store(st, base)
    except Exception as exc:
        st.error("âš™ï¸ P2 Mobile necesita configuracion.")
        st.code(str(exc))
        st.caption(
            "Para smoke local usa P2_MOBILE_LOCAL=1. "
            "Para produccion configura Supabase en Streamlit Secrets."
        )
        return None

    if not st.session_state.get("p2m_authenticated", False):
        _login_screen(st, store, backend_name)
        return None

    ctx = _build_context(st, base, store, backend_name)
    try:
        if not st.session_state.get("p2m_initialized", False):
            _initialize_cache(st, ctx)
        else:
            # Captura cambios de archivos hechos en el rerun anterior,
            # incluyendo borrado de historial desde history_engine.
            mobile_push_user_files(st, ctx)
    except Exception as exc:
        _clear_auth_state(st)
        st.error("No se pudo sincronizar el progreso.")
        st.caption(str(exc))
        return None

    return ctx


def mobile_sidebar_account(st, ctx, on_logout=None):
    st.caption("ðŸ“± P2 Mobile RC1")
    st.caption("Estudiante: " + str(ctx.get("user_code", "")))
    st.caption(
        "Backend: LOCAL TEST"
        if str(ctx.get("backend")) == "local"
        else "Backend: CLOUD"
    )
    if st.button("Cerrar sesion", use_container_width=True):
        try:
            if on_logout is not None:
                on_logout()
        except Exception:
            pass
        try:
            mobile_push_user_files(st, ctx, force=True)
        except Exception:
            pass
        _clear_auth_state(st)
        st.rerun()
