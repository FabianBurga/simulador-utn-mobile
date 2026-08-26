from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any


DETAILED_SCHEMA = "p2e1_attempt_v1"

STRATEGY_LABELS = {
    "standard_v1": "Estándar",
    "adaptive_curriculum_c0c2_v1": "Selección inteligente",
    "adaptive_v1": "Selección inteligente",
}


def _text(value: Any, fallback: str = "—") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _number(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except Exception:
        return fallback


def _is_detailed(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and record.get("schema_version") == DETAILED_SCHEMA
        and isinstance(record.get("items"), list)
    )


def _strategy_label(record: dict) -> str:
    if not _is_detailed(record):
        return "Histórico legacy"

    raw = _text(
        record.get("selection_strategy"),
        "standard_v1",
    )

    return STRATEGY_LABELS.get(
        raw,
        raw,
    )


def analyze_history(history: list[dict]) -> dict:
    if not isinstance(history, list):
        raise TypeError("history debe ser una lista")

    session_rows = []
    detailed_item_rows = []
    legacy_count = 0
    detailed_count = 0
    unknown_count = 0
    schema_counts = Counter()

    for index, record in enumerate(history, start=1):
        if not isinstance(record, dict):
            unknown_count += 1
            schema_counts["non_dict"] += 1
            continue

        schema = _text(
            record.get("schema_version"),
            "legacy",
        )
        schema_counts[schema] += 1

        detailed = _is_detailed(record)

        if detailed:
            detailed_count += 1
            kind = "Detallado"
        elif record.get("schema_version") in (None, "", "legacy"):
            legacy_count += 1
            kind = "Legacy"
        else:
            unknown_count += 1
            kind = "Otro"

        items = (
            record.get("items", [])
            if detailed
            else []
        )

        correctas = int(
            _number(
                record.get("correctas"),
                0,
            )
        )
        total = int(
            _number(
                record.get("total"),
                0,
            )
        )
        porcentaje = _number(
            record.get("porcentaje"),
            0.0,
        )

        session_rows.append({
            "N.º": index,
            "Tipo": kind,
            "Fecha": _text(record.get("fecha")),
            "Título": _text(record.get("titulo"), "Intento"),
            "Modo": _text(record.get("mode"), "—"),
            "Selección": _strategy_label(record),
            "Correctas": correctas,
            "Total": total,
            "Porcentaje": porcentaje,
            "Tiempo (s)": int(
                _number(
                    record.get("tiempo_segundos"),
                    0,
                )
            ),
            "Ítems detallados": len(items),
        })

        if not detailed:
            continue

        for item_index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue

            detailed_item_rows.append({
                "Intento": index,
                "Ítem": item_index,
                "Fecha": _text(record.get("fecha")),
                "Título": _text(
                    record.get("titulo"),
                    "Intento",
                ),
                "Modo": _text(
                    record.get("mode"),
                    "—",
                ),
                "Selección":
                    _strategy_label(record),
                "Pregunta": _text(
                    item.get("question_id")
                    or item.get("id")
                ),
                "Área": _text(
                    item.get("area"),
                    "Sin área",
                ),
                "Tema": _text(
                    item.get("topic"),
                    "Sin tema",
                ),
                "Habilidad": _text(
                    item.get("skill"),
                    "Sin habilidad",
                ),
                "Subhabilidad": _text(
                    item.get("subskill"),
                    "Sin subhabilidad",
                ),
                "Respondida": bool(
                    item.get("answered")
                ),
                "Correcta": bool(
                    item.get("correct")
                ),
                "Respuesta": _text(
                    item.get("user_answer")
                ),
                "Clave": _text(
                    item.get("correct_answer")
                ),
                "Mastery elegible": bool(
                    item.get("mastery_eligible")
                ),
                "Origen": _text(
                    item.get("content_origin"),
                    "legacy",
                ),
                "Paquete": _text(
                    item.get("content_package_id")
                ),
            })

    valid_pct = [
        row["Porcentaje"]
        for row in session_rows
        if row["Total"] > 0
    ]

    avg_pct = (
        round(
            sum(valid_pct) / len(valid_pct),
            1,
        )
        if valid_pct
        else None
    )

    return {
        "schema_version":
            "p2e5_history_view_model_v1",
        "summary": {
            "records": len(history),
            "legacy_records": legacy_count,
            "detailed_records": detailed_count,
            "unknown_records": unknown_count,
            "detailed_items":
                len(detailed_item_rows),
            "average_percentage":
                avg_pct,
            "schema_counts":
                dict(schema_counts),
        },
        "session_rows": session_rows,
        "detailed_item_rows":
            detailed_item_rows,
        "policy": {
            "legacy_preserved": True,
            "legacy_reinterpreted": False,
            "legacy_used_as_mastery": False,
            "detailed_schema":
                DETAILED_SCHEMA,
            "detailed_items_visible": True,
            "selection_strategy_visible": True,
        },
    }


def render_history_screen(
    st,
    pd,
    history: list[dict],
    history_path: Path,
) -> None:
    st.title("📈 Historial local")

    if not history:
        st.info("Aún no hay intentos guardados.")
        return

    model = analyze_history(history)
    summary = model["summary"]

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Intentos",
        summary["records"],
    )
    c2.metric(
        "Legacy",
        summary["legacy_records"],
    )
    c3.metric(
        "Detallados",
        summary["detailed_records"],
    )
    c4.metric(
        "Ítems con detalle",
        summary["detailed_items"],
    )

    if summary["legacy_records"]:
        st.caption(
            f"Se conservan {summary['legacy_records']} intentos históricos "
            "agregados. Se muestran como historial, pero no se convierten "
            "retroactivamente en evidencia de mastery."
        )

    st.subheader("Sesiones")

    sessions = pd.DataFrame(
        model["session_rows"]
    )

    if not sessions.empty:
        st.dataframe(
            sessions.iloc[::-1],
            use_container_width=True,
            hide_index=True,
        )

        chart = sessions.loc[
            sessions["Total"] > 0,
            ["Fecha", "Porcentaje"],
        ].copy()

        if len(chart) >= 2:
            chart["Fecha"] = pd.to_datetime(
                chart["Fecha"],
                errors="coerce",
            )
            chart = chart.dropna(
                subset=["Fecha"]
            )

            if len(chart) >= 2:
                chart = chart.sort_values(
                    "Fecha"
                )
                st.line_chart(
                    chart.set_index(
                        "Fecha"
                    )["Porcentaje"]
                )

    st.subheader(
        "Detalle por pregunta"
    )

    if not model["detailed_item_rows"]:
        st.info(
            "Todavía no hay intentos p2e1_attempt_v1 guardados. "
            "Los próximos intentos detallados aparecerán aquí sin "
            "modificar los 11 registros legacy existentes."
        )
    else:
        details = pd.DataFrame(
            model["detailed_item_rows"]
        )

        filter_options = [
            "Todas",
            *sorted(
                {
                    str(value)
                    for value
                    in details["Área"].dropna()
                    if str(value).strip()
                }
            ),
        ]

        selected_area = st.selectbox(
            "Filtrar detalle por área",
            filter_options,
            index=0,
        )

        if selected_area != "Todas":
            details = details[
                details["Área"]
                == selected_area
            ]

        st.dataframe(
            details.iloc[::-1],
            use_container_width=True,
            hide_index=True,
        )

    with st.expander(
        "Cómo se interpreta el historial"
    ):
        st.markdown(
            "- **Legacy**: intentos antiguos con resumen global; se preservan tal cual.\n"
            "- **Detallado**: intentos `p2e1_attempt_v1` con evidencia por pregunta.\n"
            "- La columna **Selección** distingue intentos estándar de selección inteligente cuando esa información existe.\n"
            "- El historial no convierte el refuerzo generado en evidencia de mastery."
        )

    st.divider()

    if st.button(
        "Borrar mi historial"
    ):
        history_path.write_text(
            "[]",
            encoding="utf-8",
        )
        st.rerun()


def self_test() -> dict:
    legacy = {
        "fecha": "2025-01-01T10:00:00",
        "titulo": "Legacy",
        "correctas": 5,
        "total": 10,
        "porcentaje": 50,
        "tiempo_segundos": 300,
        "areas": [],
    }

    detailed = {
        "schema_version": "p2e1_attempt_v1",
        "fecha": "2026-01-01T10:00:00",
        "titulo": "Práctica",
        "mode": "Práctica",
        "selection_strategy":
            "adaptive_curriculum_c0c2_v1",
        "correctas": 1,
        "total": 2,
        "porcentaje": 50,
        "tiempo_segundos": 120,
        "areas": [],
        "items": [
            {
                "question_id": "S1-01",
                "answered": True,
                "correct": True,
                "user_answer": "A",
                "correct_answer": "A",
                "area": "Matemática",
                "topic": "Álgebra",
                "skill": "Resolver",
                "subskill": "Lineales",
                "content_origin": "legacy",
                "mastery_eligible": True,
            },
            {
                "question_id": "R-001",
                "answered": True,
                "correct": False,
                "user_answer": "B",
                "correct_answer": "C",
                "area": "Química",
                "topic": "Refuerzo",
                "content_origin":
                    "generated_reinforcement",
                "mastery_eligible": False,
            },
        ],
    }

    model = analyze_history(
        [legacy, detailed]
    )

    if model["summary"]["legacy_records"] != 1:
        raise AssertionError("legacy count incorrecto")

    if model["summary"]["detailed_records"] != 1:
        raise AssertionError("detailed count incorrecto")

    if model["summary"]["detailed_items"] != 2:
        raise AssertionError("detail item count incorrecto")

    if (
        model["session_rows"][0]["Tipo"]
        != "Legacy"
    ):
        raise AssertionError(
            "legacy fue reinterpretado"
        )

    if (
        model["session_rows"][1]["Selección"]
        != "Selección inteligente"
    ):
        raise AssertionError(
            "selection_strategy no fue visible"
        )

    return {
        "legacy_preserved": True,
        "detailed_records_visible": True,
        "detailed_items_visible": True,
        "selection_strategy_visible": True,
        "no_legacy_retroconversion": True,
    }


if __name__ == "__main__":
    checks = self_test()
    print("P2-E5 history_engine self-test")

    for name, passed in checks.items():
        if not passed:
            raise SystemExit(f"FAIL: {name}")
        print(f"[OK] {name}")

