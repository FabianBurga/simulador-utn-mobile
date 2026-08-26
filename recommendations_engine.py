from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


SCHEMA_DETAILED = "p2e1_attempt_v1"


def _text(value: Any, default: str = "") -> str:
    value = str(value or "").strip()
    return value if value else default


def _topic_key(area: str, topic: str) -> tuple[str, str]:
    return (
        area.strip().lower(),
        topic.strip().lower(),
    )


def _priority_from_stats(
    attempts: int,
    accuracy: float,
    recent_errors: int,
) -> tuple[str, str, int]:

    # Con poca evidencia no declaramos una debilidad fuerte.
    if attempts < 3:
        return (
            "medium",
            "Conviene practicar",
            3,
        )

    weakness = 1.0 - accuracy

    score = (
        0.70 * weakness
        + 0.30 * min(1.0, recent_errors / 3.0)
    )

    if score >= 0.55:
        return (
            "high",
            "Reforzar ahora",
            5,
        )

    if score >= 0.28:
        return (
            "medium",
            "Conviene practicar",
            4,
        )

    return (
        "maintain",
        "Mantener",
        2,
    )


def build_recommendations_model(
    history: list[dict[str, Any]] | None,
) -> dict[str, Any]:

    history = history or []

    detailed = [
        record
        for record in history
        if isinstance(record, dict)
        and record.get("schema_version") == SCHEMA_DETAILED
    ]

    # Nunca reinterpretamos registros legacy.
    if not detailed:
        return {
            "cold_start": True,
            "detailed_attempts": 0,
            "evaluated_items": 0,
            "topics": 0,
            "high_count": 0,
            "medium_count": 0,
            "maintain_count": 0,
            "recommendations": [],
            "message": (
                "Todavía no existe evidencia detallada suficiente. "
                "Realiza una práctica corta para generar recomendaciones "
                "personalizadas."
            ),
        }

    topic_stats: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}

    sequence = 0
    evaluated_items = 0

    for record_index, record in enumerate(detailed):

        items = record.get("items", [])

        if not isinstance(items, list):
            continue

        # Los intentos más recientes tienen algo más de peso,
        # sin eliminar la evidencia histórica.
        if len(detailed) <= 1:
            recency_weight = 1.0
        else:
            recency_weight = (
                1.0
                + 0.5
                * record_index
                / (len(detailed) - 1)
            )

        for item in items:

            if not isinstance(item, dict):
                continue

            if item.get("answered") is not True:
                continue

            # Si una pregunta está explícitamente excluida de mastery,
            # tampoco debe generar una recomendación académica.
            if item.get("mastery_eligible") is False:
                continue

            area = _text(
                item.get("area"),
                "Sin área",
            )

            topic = _text(
                item.get("topic"),
                "Sin tema",
            )

            key = _topic_key(
                area,
                topic,
            )

            if key not in topic_stats:
                topic_stats[key] = {
                    "area": area,
                    "topic": topic,
                    "attempts": 0,
                    "correct": 0,
                    "incorrect": 0,
                    "weighted_correct": 0.0,
                    "weighted_total": 0.0,
                    "recent": [],
                    "weak_skills": Counter(),
                    "weak_subskills": Counter(),
                    "last_sequence": -1,
                }

            stat = topic_stats[key]

            correct = item.get("correct") is True

            stat["attempts"] += 1
            stat["weighted_total"] += recency_weight

            if correct:
                stat["correct"] += 1
                stat["weighted_correct"] += recency_weight
            else:
                stat["incorrect"] += 1

                skill = _text(
                    item.get("skill")
                )

                subskill = _text(
                    item.get("subskill")
                )

                if skill:
                    stat["weak_skills"][skill] += 1

                if subskill:
                    stat["weak_subskills"][subskill] += 1

            sequence += 1
            evaluated_items += 1

            stat["recent"].append(
                (
                    sequence,
                    correct,
                )
            )

            stat["last_sequence"] = sequence

    recommendations = []

    for stat in topic_stats.values():

        attempts = stat["attempts"]

        if attempts <= 0:
            continue

        accuracy = (
            stat["weighted_correct"]
            / stat["weighted_total"]
            if stat["weighted_total"] > 0
            else 0.0
        )

        recent = sorted(
            stat["recent"],
            key=lambda x: x[0],
            reverse=True,
        )[:5]

        recent_errors = sum(
            1
            for _, correct in recent
            if not correct
        )

        priority, label, suggested_questions = (
            _priority_from_stats(
                attempts,
                accuracy,
                recent_errors,
            )
        )

        weak_skill = (
            stat["weak_skills"].most_common(1)[0][0]
            if stat["weak_skills"]
            else None
        )

        weak_subskill = (
            stat["weak_subskills"].most_common(1)[0][0]
            if stat["weak_subskills"]
            else None
        )

        if attempts < 3:
            reason = (
                "La evidencia todavía es limitada; conviene "
                "practicar antes de concluir que existe una debilidad."
            )

        elif priority == "high":
            reason = (
                f"Rendimiento observado de {accuracy * 100:.0f}% "
                f"con {recent_errors} error(es) entre las "
                "interacciones recientes."
            )

        elif priority == "medium":
            reason = (
                f"Rendimiento observado de {accuracy * 100:.0f}%. "
                "El tema aún puede consolidarse."
            )

        else:
            reason = (
                f"Rendimiento observado de {accuracy * 100:.0f}%. "
                "El tema muestra una base consistente."
            )

        focus = (
            weak_subskill
            or weak_skill
            or stat["topic"]
        )

        recommendations.append(
            {
                "area": stat["area"],
                "topic": stat["topic"],
                "attempts": attempts,
                "correct": stat["correct"],
                "incorrect": stat["incorrect"],
                "accuracy": round(
                    accuracy,
                    4,
                ),
                "accuracy_pct": round(
                    accuracy * 100,
                    1,
                ),
                "recent_errors": recent_errors,
                "priority": priority,
                "label": label,
                "suggested_questions": (
                    suggested_questions
                ),
                "focus": focus,
                "reason": reason,
                "last_sequence": (
                    stat["last_sequence"]
                ),
            }
        )

    priority_order = {
        "high": 0,
        "medium": 1,
        "maintain": 2,
    }

    recommendations.sort(
        key=lambda item: (
            priority_order.get(
                item["priority"],
                99,
            ),
            item["accuracy"],
            -item["recent_errors"],
            -item["last_sequence"],
            item["area"].lower(),
            item["topic"].lower(),
        )
    )

    high_count = sum(
        x["priority"] == "high"
        for x in recommendations
    )

    medium_count = sum(
        x["priority"] == "medium"
        for x in recommendations
    )

    maintain_count = sum(
        x["priority"] == "maintain"
        for x in recommendations
    )

    return {
        "cold_start": False,
        "detailed_attempts": len(detailed),
        "evaluated_items": evaluated_items,
        "topics": len(recommendations),
        "high_count": high_count,
        "medium_count": medium_count,
        "maintain_count": maintain_count,
        "recommendations": recommendations,
        "message": None,
    }


def render_recommendations(
    st,
    pd,
    model: dict[str, Any],
) -> None:

    st.title("🧭 Recomendaciones")

    st.caption(
        "Recomendaciones construidas únicamente con evidencia "
        "de intentos detallados. Los registros legacy no se "
        "reinterpretan."
    )

    if model.get("cold_start"):

        st.info(
            model.get(
                "message",
                "Todavía no existe evidencia suficiente.",
            )
        )

        st.markdown(
            "**Primer paso sugerido:** realiza una práctica "
            "corta de 5 preguntas para obtener evidencia inicial."
        )

        return

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Intentos detallados",
        model["detailed_attempts"],
    )

    c2.metric(
        "Ítems evaluados",
        model["evaluated_items"],
    )

    c3.metric(
        "Temas observados",
        model["topics"],
    )

    c4.metric(
        "Reforzar ahora",
        model["high_count"],
    )

    st.divider()

    recommendations = model["recommendations"]

    groups = [
        (
            "high",
            "🔴 Reforzar ahora",
            (
                "Prioridad alta según errores y "
                "rendimiento observado."
            ),
        ),
        (
            "medium",
            "🟡 Conviene practicar",
            (
                "Temas que necesitan más evidencia "
                "o consolidación."
            ),
        ),
        (
            "maintain",
            "🟢 Mantener",
            (
                "Temas con rendimiento consistente; "
                "requieren mantenimiento ocasional."
            ),
        ),
    ]

    for priority, title, caption in groups:

        rows = [
            item
            for item in recommendations
            if item["priority"] == priority
        ]

        if not rows:
            continue

        st.subheader(title)

        st.caption(caption)

        for item in rows:

            with st.container(border=True):

                left, right = st.columns(
                    [3, 1]
                )

                with left:

                    st.markdown(
                        f"### {item['topic']}"
                    )

                    st.caption(
                        item["area"]
                    )

                    st.write(
                        item["reason"]
                    )

                    st.write(
                        "**Enfoque sugerido:** "
                        f"{item['focus']}"
                    )

                with right:

                    st.metric(
                        "Rendimiento",
                        f"{item['accuracy_pct']:.0f}%",
                    )

                    st.metric(
                        "Práctica sugerida",
                        (
                            f"{item['suggested_questions']} "
                            "preguntas"
                        ),
                    )

        st.divider()

    # Vista general compacta
    table_rows = []

    for item in recommendations:

        label_map = {
            "high": "Reforzar ahora",
            "medium": "Conviene practicar",
            "maintain": "Mantener",
        }

        table_rows.append(
            {
                "Área": item["area"],
                "Tema": item["topic"],
                "Prioridad": label_map[
                    item["priority"]
                ],
                "Rendimiento (%)": (
                    item["accuracy_pct"]
                ),
                "Intentos": item["attempts"],
                "Errores": item["incorrect"],
                "Práctica sugerida": (
                    item["suggested_questions"]
                ),
            }
        )

    if table_rows:

        with st.expander(
            "Ver resumen completo por tema"
        ):
            df = pd.DataFrame(
                table_rows
            )

            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
            )
