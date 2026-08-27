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


def _login_screen(st, store, backend_name: str):
    st.title("ðŸ“± Simulador UTN")
    st.caption("P2 Mobile RC1 Â· progreso independiente por estudiante")
    st.info(
        "Usa un codigo de estudiante que no revele informacion personal. "
        "Si el codigo no existe, se creara automaticamente."
    )

    locked_until = float(st.session_state.get("p2m_locked_until", 0))
    now = time.time()
    if locked_until > now:
        remaining = int(locked_until - now) + 1
        st.warning(f"Espera {remaining}s antes de volver a intentar.")
        return None

    with st.form("p2_mobile_login", clear_on_submit=False):
        raw_code = st.text_input(
            "Codigo de estudiante",
            placeholder="Ejemplo: UTN2026A1",
            max_chars=24,
        )
        pin = st.text_input(
            "PIN",
            type="password",
            max_chars=12,
            help="Solo numeros, entre 4 y 12 digitos.",
        )
        submitted = st.form_submit_button(
            "Entrar / Crear cuenta",
            use_container_width=True,
        )

    if not submitted:
        return None

    code = _normalize_code(raw_code)
    if not _valid_code(code):
        st.error("El codigo debe tener 4-24 caracteres: letras, numeros, _ o -.")
        return None
    if not _valid_pin(pin):
        st.error("El PIN debe tener entre 4 y 12 digitos.")
        return None

    try:
        row = store.find_by_code(code)
        created = False
        if row is None:
            row = store.create_user(code, pin)
            created = True
        else:
            if not _verify_pin(
                pin,
                str(row.get("pin_salt", "")),
                str(row.get("pin_hash", "")),
            ):
                failures = int(st.session_state.get("p2m_login_failures", 0)) + 1
                st.session_state["p2m_login_failures"] = failures
                if failures >= 5:
                    st.session_state["p2m_locked_until"] = time.time() + 30
                    st.session_state["p2m_login_failures"] = 0
                st.error("Codigo o PIN incorrecto.")
                return None

        st.session_state["p2m_authenticated"] = True
        st.session_state["p2m_user_id"] = str(row["id"])
        st.session_state["p2m_user_code"] = code
        st.session_state["p2m_backend"] = backend_name
        st.session_state["p2m_session_id"] = str(uuid.uuid4())
        st.session_state["p2m_initialized"] = False
        st.session_state["p2m_login_failures"] = 0
        if created:
            st.success("Cuenta creada.")
        st.rerun()
    except Exception as exc:
        st.error("No se pudo iniciar la sesion.")
        st.caption(str(exc))
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
