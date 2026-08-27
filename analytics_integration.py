from __future__ import annotations

from typing import Any

from analytics_engine import build_engine_for_mobile, canonical_answer_key

APP_VERSION = "P2-MOBILE-M5A.4"
ENGINE_KEY = "_p2_analytics_engine"
ENGINE_USER_KEY = "_p2_analytics_user_id"


def _engine(st):
    return st.session_state.get(ENGINE_KEY)


def _safe(action, fallback=None):
    try:
        return action()
    except Exception:
        return fallback


def ensure_analytics(st, mobile_ctx: dict[str, Any], *, app_version: str = APP_VERSION):
    user_id = str(mobile_ctx.get("user_id") or "").strip()
    if not user_id:
        return None

    existing = _engine(st)
    existing_user = str(st.session_state.get(ENGINE_USER_KEY) or "")
    if existing is not None and existing_user != user_id:
        _safe(lambda: existing.end_session("user_switch"))
        st.session_state.pop(ENGINE_KEY, None)
        st.session_state.pop(ENGINE_USER_KEY, None)
        existing = None

    if existing is None:
        engine = _safe(lambda: build_engine_for_mobile(st, mobile_ctx, app_version=app_version))
        if engine is None:
            return None
        st.session_state[ENGINE_KEY] = engine
        st.session_state[ENGINE_USER_KEY] = user_id
        client_instance_id = st.session_state.get("p2m_session_id")
        _safe(lambda: engine.start_session(client_instance_id=client_instance_id))
        _safe(lambda: engine.track(
            "LOGIN_SUCCESS",
            metadata={"backend": str(mobile_ctx.get("backend") or "")},
            critical=True,
            semantic_scope="login_success",
        ))
        _safe(engine.reconcile_stale)
    else:
        engine = existing

    if hasattr(st, "fragment"):
        try:
            @st.fragment(run_every=60.0)
            def _p2_heartbeat_fragment():
                current = _engine(st)
                if current is not None:
                    _safe(current.heartbeat)
            _p2_heartbeat_fragment()
        except Exception:
            _safe(engine.heartbeat)
    else:
        _safe(engine.heartbeat)
    return engine


def analytics_page_view(st, page: Any, mode: Any = None) -> bool:
    engine = _engine(st)
    if engine is None:
        return False
    page_text = str(page or "").strip() or "Unknown"
    mode_text = None if mode is None else str(mode)
    changed = bool(_safe(lambda: engine.page_view(page_text, mode_text), False))
    if changed and "recomend" in page_text.lower():
        _safe(engine.recommendations_viewed)
    return changed


def _selection_strategy(st):
    for key in (
        "selection_strategy",
        "selection_strategy_id",
        "adaptive_selection_strategy",
        "p2_selection_strategy",
    ):
        value = st.session_state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def analytics_start_attempt(st, *, mode: Any, title: Any, questions) -> str | None:
    engine = _engine(st)
    if engine is None:
        return None
    if engine.attempt_id:
        return engine.attempt_id
    mode_text = str(mode or "Desconocido")
    attempt_type = "practice" if mode_text.strip().lower() in {"práctica", "practica"} else "exam"
    return _safe(lambda: engine.start_attempt(
        attempt_type=attempt_type,
        mode=mode_text,
        title=str(title or "") or None,
        question_count=len(questions or []),
        selection_strategy=_selection_strategy(st),
    ))


def _ensure_attempt_for_runtime(st):
    engine = _engine(st)
    if engine is None or engine.attempt_id:
        return engine
    questions = st.session_state.get("exam_questions") or []
    if not questions:
        return engine
    analytics_start_attempt(
        st,
        mode=st.session_state.get("mode", "Desconocido"),
        title=st.session_state.get("exam_title", ""),
        questions=questions,
    )
    return _engine(st)


def analytics_question_view(st, question: dict[str, Any], order: int) -> bool:
    engine = _ensure_attempt_for_runtime(st)
    return bool(_safe(lambda: engine.question_view(question, order), False)) if engine else False


def analytics_answer_selected(st, question: dict[str, Any], answer: Any) -> bool:
    engine = _ensure_attempt_for_runtime(st)
    return bool(_safe(lambda: engine.answer_selected(question, answer), False)) if engine else False


def analytics_set_flag(st, question: dict[str, Any], flagged: bool) -> bool:
    engine = _ensure_attempt_for_runtime(st)
    return bool(_safe(lambda: engine.set_flag(question, bool(flagged)), False)) if engine else False


def _build_detailed_items(questions, answers):
    rows = []
    for order, q in enumerate(questions, start=1):
        qid = str(q.get("id") or "").strip()
        if not qid:
            continue
        user = answers.get(qid)
        answered = user not in (None, "", "—")
        correct_key = canonical_answer_key(q.get("answer"))
        user_key = canonical_answer_key(user) if answered else ""
        rows.append({
            "question_id": qid,
            "question_order": order,
            "answered": answered,
            "correct": bool(answered and user_key == correct_key),
            "user_answer": user if answered else None,
            "correct_answer": q.get("answer"),
            "area": q.get("subject", q.get("area")),
            "topic": q.get("topic"),
            "skill": q.get("skill"),
            "subskill": q.get("subskill"),
        })
    return rows


def analytics_complete_attempt(st, *, qs, answers, correct: int, total: int, pct: float, detailed_items=None) -> bool:
    engine = _ensure_attempt_for_runtime(st)
    if engine is None or not engine.attempt_id:
        return False
    details = detailed_items if isinstance(detailed_items, list) else None
    if not details:
        details = _build_detailed_items(list(qs or []), dict(answers or {}))
    _safe(engine.results_viewed)
    return bool(_safe(lambda: engine.complete_attempt(
        correct_count=int(correct),
        total=int(total),
        percentage=float(pct),
        detailed_items=details,
    ), False))


def analytics_abandon_attempt(st, reason: str = "reset_exam") -> bool:
    engine = _engine(st)
    if engine is None or not engine.attempt_id:
        return False
    return bool(_safe(lambda: engine.abandon_attempt(reason=reason), False))


def analytics_logout(st) -> bool:
    engine = _engine(st)
    ok = False
    if engine is not None:
        ok = bool(_safe(lambda: engine.end_session("logout"), False))
    st.session_state.pop(ENGINE_KEY, None)
    st.session_state.pop(ENGINE_USER_KEY, None)
    return ok


def analytics_health(st) -> dict[str, Any]:
    engine = _engine(st)
    if engine is None:
        return {"healthy": False, "reason": "engine_unavailable"}
    return _safe(engine.health_snapshot, {"healthy": False, "reason": "snapshot_failed"})
