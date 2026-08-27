from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from admin_backend import (
    ADMIN_VERSION,
    AdminDataStore,
    pct,
    safe_ratio,
    seconds_human,
    verify_admin_password,
)

EC_TZ = ZoneInfo("America/Guayaquil")

st.set_page_config(
    page_title="P2 Master Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    [data-testid="stMainBlockContainer"] {max-width: 1550px;}
    .p2-note {opacity:.72; font-size:.88rem;}
    @media (max-width: 768px) {
      [data-testid="stMainBlockContainer"] {
        padding-left: .65rem;
        padding-right: .65rem;
      }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def secret_section(name: str) -> dict:
    try:
        return dict(st.secrets[name])
    except Exception:
        return {}


def login() -> bool:
    if st.session_state.get("p2_admin_authenticated"):
        return True

    admin_cfg = secret_section("admin")
    encoded_hash = str(admin_cfg.get("password_hash", "")).strip()
    if not encoded_hash:
        st.error("Falta configurar [admin].password_hash en Streamlit Secrets.")
        st.stop()

    st.title("🔐 P2 Master Dashboard")
    st.caption("Acceso administrativo independiente del acceso de estudiantes.")

    with st.form("p2_admin_login"):
        password = st.text_input("Clave maestra", type="password")
        submitted = st.form_submit_button("Entrar", use_container_width=True)

    if submitted:
        failures = int(st.session_state.get("p2_admin_failures", 0))
        if failures >= 8:
            st.error("Demasiados intentos fallidos en esta sesión.")
            st.stop()
        if verify_admin_password(password, encoded_hash):
            st.session_state["p2_admin_authenticated"] = True
            st.session_state["p2_admin_failures"] = 0
            st.rerun()
        st.session_state["p2_admin_failures"] = failures + 1
        st.error("Clave incorrecta.")
    return False


def build_store() -> AdminDataStore:
    cfg = secret_section("supabase")
    url = str(cfg.get("url", "")).strip()
    secret_key = str(
        cfg.get("secret_key", cfg.get("service_role_key", ""))
    ).strip()
    if not url or not secret_key:
        st.error("Faltan los Secrets de Supabase.")
        st.stop()
    return AdminDataStore.from_supabase(url, secret_key)


def frame(rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows or []))


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def metric_row(values):
    cols = st.columns(len(values))
    for col, item in zip(cols, values):
        label, value, help_text = item
        col.metric(label, value, help=help_text)


def local_time_text(value) -> str:
    if not value:
        return "—"
    try:
        dt = pd.to_datetime(value, utc=True).to_pydatetime().astimezone(EC_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def overview(store: AdminDataStore):
    students = store.students()
    live = store.live_sessions()
    attempts = store.attempts(limit=5000)
    health = store.health()

    answered = sum(int(a.get("questions_answered") or 0) for a in attempts)
    correct = sum(int(a.get("correct_count") or 0) for a in attempts)
    completed = sum(1 for a in attempts if a.get("status") == "completed")
    abandoned = sum(1 for a in attempts if a.get("status") == "abandoned")

    metric_row([
        ("Estudiantes", len(students), "Códigos registrados."),
        ("Activos ahora", len(live), "Actividad observada recientemente; no presencia exacta."),
        ("Intentos", len(attempts), "Prácticas + exámenes iniciados."),
        ("Completados", completed, None),
        ("Abandonados", abandoned, None),
        ("Precisión global", pct(safe_ratio(correct, answered)), "Aciertos / preguntas respondidas."),
    ])

    daily = frame(store.daily_metrics(30))
    if not daily.empty:
        daily["day"] = pd.to_datetime(daily["day"], errors="coerce")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### Usuarios activos por día")
            st.line_chart(daily.set_index("day")[["active_users"]])
        with c2:
            st.markdown("#### Inicios y finalizaciones")
            cols = [
                c for c in
                ["practices_started", "practices_completed", "exams_started", "exams_completed"]
                if c in daily.columns
            ]
            if cols:
                st.line_chart(daily.set_index("day")[cols])

        st.markdown("#### Métricas diarias")
        show = daily.copy()
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.download_button(
            "Exportar métricas diarias CSV",
            csv_bytes(show),
            file_name="p2_daily_metrics.csv",
            mime="text/csv",
        )

    errors = int(health.get("observed_errors_24h") or 0)
    if errors:
        st.warning(f"Se observaron {errors} errores de aplicación en las últimas 24 h.")


def live_now(store: AdminDataStore):
    rows = store.live_sessions()
    st.caption(
        "Estado observado por heartbeat. ACTIVE/IDLE es una aproximación y no equivale "
        "a asegurar que la pestaña esté visible."
    )
    if not rows:
        st.info("No hay sesiones activas o idle recientes.")
        return

    for row in rows:
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1.2, 1.1, 1])
            c1.markdown(f"**{row.get('user_code','')}**")
            c2.write(str(row.get("observed_state") or "").upper())
            c3.write(row.get("current_mode") or "—")
            c4.write(row.get("current_page") or "—")
            c5.write(seconds_human(row.get("seconds_since_seen")))
            st.caption(
                f"Pregunta: {row.get('current_question_id') or '—'} · "
                f"Página abierta: {seconds_human(row.get('page_open_seconds'))} · "
                f"Interacción activa: {seconds_human(row.get('interaction_active_seconds'))}"
            )


def live_auto(store: AdminDataStore):
    if hasattr(st, "fragment"):
        @st.fragment(run_every="30s")
        def _live_fragment():
            live_now(store)
        _live_fragment()
    else:
        live_now(store)


def students_tab(store: AdminDataStore):
    rows = store.students()
    df = frame(rows)
    if df.empty:
        st.info("No hay estudiantes.")
        return

    search = st.text_input("Buscar código", key="student_search")
    view = df.copy()
    if search.strip():
        view = view[
            view["user_code"].astype(str).str.contains(search.strip(), case=False, na=False)
        ]

    table_cols = [
        c for c in [
            "user_code", "activity_segment", "sessions_total", "active_days",
            "practices_started", "exams_started", "questions_answered",
            "answer_accuracy_pct", "active_seconds", "last_seen_at"
        ] if c in view.columns
    ]
    display = view[table_cols].copy()
    if "last_seen_at" in display.columns:
        display["last_seen_at"] = display["last_seen_at"].apply(local_time_text)

    st.dataframe(display, use_container_width=True, hide_index=True)
    st.download_button(
        "Exportar estudiantes CSV",
        csv_bytes(view),
        file_name="p2_students.csv",
        mime="text/csv",
    )

    codes = view["user_code"].astype(str).tolist()
    if not codes:
        return
    code = st.selectbox("Detalle de estudiante", codes)
    selected = next(r for r in rows if str(r.get("user_code")) == code)
    user_id = str(selected["user_id"])

    metric_row([
        ("Sesiones", int(selected.get("sessions_total") or 0), None),
        ("Días activos", int(selected.get("active_days") or 0), None),
        ("Prácticas", int(selected.get("practices_started") or 0), None),
        ("Exámenes", int(selected.get("exams_started") or 0), None),
        ("Respondidas", int(selected.get("questions_answered") or 0), None),
        ("Precisión", pct(selected.get("answer_accuracy_pct")), None),
    ])

    st.caption(
        f"Página abierta acumulada: {seconds_human(selected.get('page_open_seconds'))} · "
        f"Interacción activa acumulada: {seconds_human(selected.get('active_seconds'))} · "
        f"Segmento: {selected.get('activity_segment')}"
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Intentos")
        attempts = frame(store.attempts(user_id=user_id, limit=500))
        if attempts.empty:
            st.info("Sin intentos.")
        else:
            st.dataframe(attempts, use_container_width=True, hide_index=True)
    with c2:
        st.markdown("#### Sesiones")
        sessions = frame(store.sessions(user_id=user_id, limit=500))
        if sessions.empty:
            st.info("Sin sesiones.")
        else:
            st.dataframe(sessions, use_container_width=True, hide_index=True)

    timeline = frame(store.user_timeline(user_id, 500))
    if not timeline.empty:
        wanted = [
            x for x in
            ["occurred_at", "event_name", "page", "mode", "question_id", "sequence_no"]
            if x in timeline.columns
        ]
        st.markdown("#### Timeline")
        timeline = timeline[wanted].copy()
        if "occurred_at" in timeline.columns:
            timeline["occurred_at"] = timeline["occurred_at"].apply(local_time_text)
        st.dataframe(timeline, use_container_width=True, hide_index=True)


def sessions_tab(store: AdminDataStore):
    df = frame(store.sessions(limit=5000))
    if df.empty:
        st.info("No hay sesiones.")
        return

    c1, c2 = st.columns(2)
    statuses = ["Todos"] + sorted(df["status"].dropna().astype(str).unique().tolist())
    status_filter = c1.selectbox("Estado de sesión", statuses)
    code_search = c2.text_input("Código de estudiante", key="session_code")

    view = df.copy()
    if status_filter != "Todos":
        view = view[view["status"] == status_filter]
    if code_search.strip():
        view = view[
            view["user_code"].astype(str).str.contains(code_search.strip(), case=False, na=False)
        ]

    cols = [
        c for c in [
            "user_code", "status", "observed_state", "started_at", "ended_at",
            "ended_reason", "current_mode", "current_page", "page_open_seconds",
            "interaction_active_seconds", "idle_seconds", "events_count",
            "heartbeat_count", "app_version"
        ] if c in view.columns
    ]
    shown = view[cols].copy()
    for c in ["started_at", "ended_at"]:
        if c in shown.columns:
            shown[c] = shown[c].apply(local_time_text)
    st.dataframe(shown, use_container_width=True, hide_index=True)
    st.caption(
        "page_open_seconds mide tiempo observado con la sesión abierta; "
        "interaction_active_seconds es la aproximación de actividad/interacción."
    )
    st.download_button(
        "Exportar sesiones CSV",
        csv_bytes(view),
        file_name="p2_sessions.csv",
        mime="text/csv",
    )


def attempts_tab(store: AdminDataStore):
    df = frame(store.attempts(limit=5000))
    if df.empty:
        st.info("No hay intentos.")
        return

    c1, c2, c3 = st.columns(3)
    types = ["Todos"] + sorted(df["attempt_type"].dropna().astype(str).unique().tolist())
    statuses = ["Todos"] + sorted(df["status"].dropna().astype(str).unique().tolist())
    codes = ["Todos"] + sorted(df["user_code"].dropna().astype(str).unique().tolist())
    type_filter = c1.selectbox("Tipo", types)
    status_filter = c2.selectbox("Estado", statuses)
    code_filter = c3.selectbox("Estudiante", codes)

    view = df.copy()
    if type_filter != "Todos":
        view = view[view["attempt_type"] == type_filter]
    if status_filter != "Todos":
        view = view[view["status"] == status_filter]
    if code_filter != "Todos":
        view = view[view["user_code"] == code_filter]

    st.dataframe(view, use_container_width=True, hide_index=True)
    st.download_button(
        "Exportar intentos CSV",
        csv_bytes(view),
        file_name="p2_attempts.csv",
        mime="text/csv",
    )

    if not view.empty:
        attempt_id = st.selectbox("Ver preguntas del intento", view["attempt_id"].astype(str))
        items = frame(store.attempt_items(attempt_id))
        if not items.empty:
            st.dataframe(items, use_container_width=True, hide_index=True)


def questions_tab(store: AdminDataStore):
    df = frame(store.questions())
    if df.empty:
        st.info("Todavía no hay actividad suficiente por pregunta.")
        return

    st.caption(
        "Accuracy, tiempo, cambios y banderas son señales para investigar dificultad/fricción. "
        "Con muestras pequeñas no deben interpretarse como diagnóstico definitivo."
    )

    c1, c2 = st.columns(2)
    min_attempts = c1.number_input(
        "Mínimo de intentos observados",
        min_value=1,
        max_value=10000,
        value=1,
        step=1,
    )
    areas = ["Todas"] + sorted(df["area"].fillna("Sin área").astype(str).unique().tolist())
    area_filter = c2.selectbox("Área", areas)

    view = df[df["attempts_seen"].fillna(0) >= min_attempts].copy()
    if area_filter != "Todas":
        view = view[view["area"].fillna("Sin área").astype(str) == area_filter]

    sort_options = [
        x for x in
        ["accuracy_pct", "median_response_seconds", "flagged_pct", "answer_changed_pct", "attempts_seen"]
        if x in view.columns
    ]
    if sort_options:
        sort_by = st.selectbox("Ordenar por", sort_options)
        view = view.sort_values(
            sort_by,
            ascending=(sort_by == "accuracy_pct"),
            na_position="last",
        )

    st.dataframe(view, use_container_width=True, hide_index=True)
    st.download_button(
        "Exportar preguntas CSV",
        csv_bytes(view),
        file_name="p2_question_metrics.csv",
        mime="text/csv",
    )


def areas_tab(store: AdminDataStore):
    df = frame(store.areas())
    if df.empty:
        st.info("Aún no hay métricas por área.")
        return

    metric_row([
        ("Áreas observadas", len(df), None),
        ("Respuestas", int(df["responses"].fillna(0).sum()), None),
        ("Aciertos", int(df["correct_responses"].fillna(0).sum()), None),
        ("Precisión", pct(safe_ratio(
            df["correct_responses"].fillna(0).sum(),
            df["responses"].fillna(0).sum(),
        )), None),
    ])

    st.dataframe(df, use_container_width=True, hide_index=True)
    if "accuracy_pct" in df.columns:
        chart = df.set_index("area")[["accuracy_pct"]].sort_values("accuracy_pct")
        st.bar_chart(chart)
    st.download_button(
        "Exportar áreas CSV",
        csv_bytes(df),
        file_name="p2_area_metrics.csv",
        mime="text/csv",
    )


def funnel_retention(store: AdminDataStore):
    funnel = store.funnel()
    if funnel:
        metric_row([
            ("Registrados", int(funnel.get("registered_users") or 0), None),
            ("Con sesión", int(funnel.get("users_with_session") or 0), None),
            ("Inició intento", int(funnel.get("users_started_attempt") or 0), None),
            ("Respondió ≥5", int(funnel.get("users_answered_5") or 0), None),
            ("Práctica completa", int(funnel.get("users_completed_practice") or 0), None),
            ("Examen completo", int(funnel.get("users_completed_exam") or 0), None),
        ])

    retention = frame(store.retention())
    if retention.empty:
        st.info("Aún no hay suficiente historial para retención.")
        return

    total = len(retention)
    d1 = int(retention["returned_d1"].fillna(False).sum())
    d7 = int(retention["returned_within_7d"].fillna(False).sum())
    c1, c2 = st.columns(2)
    c1.metric("Retención D1", pct(safe_ratio(d1, total)))
    c2.metric("Retorno ≤7 días", pct(safe_ratio(d7, total)))
    st.dataframe(retention, use_container_width=True, hide_index=True)


def quality_tab(store: AdminDataStore):
    q = store.data_quality()
    if not q:
        st.info("No hay información de calidad.")
        return

    metric_row([
        ("Duplicados sequence", int(q.get("duplicate_sequence_pairs") or 0), None),
        ("Duplicados idempotencia", int(q.get("duplicate_idempotency_keys") or 0), None),
        ("Gaps generación validada", int(q.get("current_version_sessions_with_sequence_gaps") or 0), None),
        ("Eventos attempt inválidos", int(q.get("invalid_attempt_events") or 0), None),
        ("Eventos question inválidos", int(q.get("invalid_question_events") or 0), None),
        ("Ordenes inválidos", int(q.get("invalid_question_orders") or 0), None),
    ])

    critical_fields = [
        "duplicate_sequence_pairs",
        "duplicate_idempotency_keys",
        "current_version_sessions_with_sequence_gaps",
        "current_version_missing_sequence_numbers",
        "invalid_attempt_events",
        "invalid_question_events",
        "invalid_completed_attempts",
        "invalid_terminal_sessions",
        "stale_open_sessions",
        "invalid_question_orders",
    ]
    critical = sum(int(q.get(k) or 0) for k in critical_fields)

    if critical == 0:
        st.success("DATA QUALITY: PASS para la generación validada.")
    else:
        st.error("DATA QUALITY: existen anomalías en la generación validada.")

    legacy = int(q.get("legacy_sessions_with_sequence_gaps") or 0)
    if legacy:
        st.info(
            f"Hay {legacy} sesión(es) pre-validación con gaps históricos. "
            "Se conservan como datos de prueba y no contaminan el criterio de producción validada."
        )

    st.json(q)


def health_tab(store: AdminDataStore):
    h = store.health()
    metric_row([
        ("Eventos 24 h", int(h.get("events_24h") or 0), None),
        ("Sesiones 24 h", int(h.get("sessions_24h") or 0), None),
        ("Intentos 24 h", int(h.get("attempts_24h") or 0), None),
        ("Errores observados", int(h.get("observed_errors_24h") or 0), None),
        ("Sesiones stale", int(h.get("stale_open_sessions") or 0), None),
        ("Intentos stale", int(h.get("stale_in_progress_attempts") or 0), None),
    ])

    invalid = int(h.get("invalid_question_events") or 0) + int(h.get("invalid_attempt_events") or 0)
    if invalid == 0 and int(h.get("observed_errors_24h") or 0) == 0:
        st.success("Telemetría observada: HEALTHY")
    else:
        st.warning("Hay señales observadas que requieren revisión.")
    st.caption(
        "Este estado describe errores y consistencia observados por la aplicación; "
        "no representa un SLA ni monitoreo completo de infraestructura."
    )
    st.json(h)


if not login():
    st.stop()

store = build_store()

with st.sidebar:
    st.title("📊 P2 Master")
    st.caption(ADMIN_VERSION)
    st.caption(
        "Ecuador · " + datetime.now(timezone.utc).astimezone(EC_TZ).strftime("%Y-%m-%d %H:%M")
    )
    if st.button("Actualizar datos", use_container_width=True):
        st.rerun()
    if st.button("Cerrar administración", use_container_width=True):
        st.session_state.pop("p2_admin_authenticated", None)
        st.rerun()

st.title("P2 Master Dashboard")
st.caption(
    "Analítica académica + producto basada en p2_analytics_v1. "
    "No recopila PIN, IP, GPS ni fingerprinting."
)

tabs = st.tabs([
    "Overview",
    "Live Now",
    "Students",
    "Sessions",
    "Attempts",
    "Questions",
    "Areas",
    "Funnel / Retention",
    "Data Quality",
    "System Health",
])

with tabs[0]:
    overview(store)
with tabs[1]:
    live_auto(store)
with tabs[2]:
    students_tab(store)
with tabs[3]:
    sessions_tab(store)
with tabs[4]:
    attempts_tab(store)
with tabs[5]:
    questions_tab(store)
with tabs[6]:
    areas_tab(store)
with tabs[7]:
    funnel_retention(store)
with tabs[8]:
    quality_tab(store)
with tabs[9]:
    health_tab(store)
