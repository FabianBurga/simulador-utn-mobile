from __future__ import annotations

from datetime import datetime, timezone

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

st.set_page_config(
    page_title="P2 Master Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    [data-testid="stMainBlockContainer"] {max-width: 1500px;}
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


def overview(store: AdminDataStore):
    students = store.students()
    live = store.live_sessions()
    attempts = store.attempts(limit=5000)
    health = store.health()
    answered = sum(int(a.get("questions_answered") or 0) for a in attempts)
    correct = sum(int(a.get("correct_count") or 0) for a in attempts)
    completed = sum(1 for a in attempts if a.get("status") == "completed")

    metric_row([
        ("Estudiantes", len(students), None),
        ("Activos ahora", len(live), None),
        ("Intentos", len(attempts), None),
        ("Completados", completed, None),
        ("Precisión global", pct(safe_ratio(correct, answered)), None),
        ("Errores 24 h", int(health.get("observed_errors_24h") or 0), None),
    ])

    daily = frame(store.daily_metrics(30))
    if not daily.empty:
        daily["day"] = pd.to_datetime(daily["day"], errors="coerce")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### Usuarios activos")
            st.line_chart(daily.set_index("day")[["active_users"]])
        with c2:
            st.markdown("#### Prácticas / Exámenes")
            cols = [
                c for c in
                ["practices_started", "practices_completed", "exams_started", "exams_completed"]
                if c in daily.columns
            ]
            if cols:
                st.line_chart(daily.set_index("day")[cols])
        st.download_button(
            "Exportar métricas diarias CSV",
            csv_bytes(daily),
            file_name="p2_daily_metrics.csv",
            mime="text/csv",
        )


def live_now(store: AdminDataStore):
    rows = store.live_sessions()
    if not rows:
        st.info("No hay sesiones activas o inactivas recientes.")
        return
    for row in rows:
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([1.4, 1, 1.2, 1, 1])
            c1.markdown(f"**{row.get('user_code','')}**")
            c2.write(str(row.get("online_state") or "").upper())
            c3.write(row.get("current_mode") or "—")
            c4.write(row.get("current_page") or "—")
            c5.write(seconds_human(row.get("seconds_since_seen")))
            st.caption(
                f"Pregunta {row.get('current_question_id') or '—'} · "
                f"Tiempo activo {seconds_human(row.get('interaction_active_seconds'))}"
            )


def students_tab(store: AdminDataStore):
    rows = store.students()
    df = frame(rows)
    if df.empty:
        st.info("No hay estudiantes.")
        return

    search = st.text_input("Buscar código")
    view = df.copy()
    if search.strip():
        view = view[
            view["user_code"].astype(str).str.contains(search.strip(), case=False, na=False)
        ]

    st.dataframe(view, use_container_width=True, hide_index=True)
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
        f"Tiempo activo acumulado: {seconds_human(selected.get('active_seconds'))} · "
        f"Segmento: {selected.get('activity_segment')}"
    )

    timeline = frame(store.user_timeline(user_id, 500))
    if not timeline.empty:
        wanted = [
            x for x in
            ["occurred_at", "event_name", "page", "mode", "question_id", "sequence_no"]
            if x in timeline.columns
        ]
        st.markdown("#### Timeline")
        st.dataframe(timeline[wanted], use_container_width=True, hide_index=True)

    attempts = frame(store.attempts(user_id=user_id, limit=500))
    if not attempts.empty:
        st.markdown("#### Intentos")
        st.dataframe(attempts, use_container_width=True, hide_index=True)


def attempts_tab(store: AdminDataStore):
    df = frame(store.attempts(limit=5000))
    if df.empty:
        st.info("No hay intentos.")
        return

    c1, c2 = st.columns(2)
    types = ["Todos"] + sorted(df["attempt_type"].dropna().astype(str).unique().tolist())
    statuses = ["Todos"] + sorted(df["status"].dropna().astype(str).unique().tolist())
    type_filter = c1.selectbox("Tipo", types)
    status_filter = c2.selectbox("Estado", statuses)

    view = df.copy()
    if type_filter != "Todos":
        view = view[view["attempt_type"] == type_filter]
    if status_filter != "Todos":
        view = view[view["status"] == status_filter]

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
        "Estas métricas son señales para investigar dificultad o fricción; "
        "no prueban por sí solas que una pregunta sea defectuosa."
    )

    min_attempts = st.number_input(
        "Mínimo de intentos observados",
        min_value=1,
        max_value=10000,
        value=1,
        step=1,
    )
    view = df[df["attempts_seen"].fillna(0) >= min_attempts].copy()
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
        st.warning("Hay señales que requieren revisión.")
    st.json(h)


if not login():
    st.stop()

store = build_store()

with st.sidebar:
    st.title("📊 P2 Master")
    st.caption(ADMIN_VERSION)
    st.caption(datetime.now(timezone.utc).strftime("UTC · %Y-%m-%d %H:%M"))
    if st.button("Actualizar datos", use_container_width=True):
        st.rerun()
    if st.button("Cerrar administración", use_container_width=True):
        st.session_state.pop("p2_admin_authenticated", None)
        st.rerun()

st.title("P2 Master Dashboard")
st.caption(
    "Analítica de aprendizaje y producto basada en p2_analytics_v1. "
    "No contiene PIN, IP, GPS ni fingerprinting."
)

tabs = st.tabs([
    "Overview",
    "Live Now",
    "Students",
    "Attempts",
    "Questions",
    "Funnel / Retention",
    "System Health",
])

with tabs[0]:
    overview(store)
with tabs[1]:
    live_now(store)
with tabs[2]:
    students_tab(store)
with tabs[3]:
    attempts_tab(store)
with tabs[4]:
    questions_tab(store)
with tabs[5]:
    funnel_retention(store)
with tabs[6]:
    health_tab(store)
