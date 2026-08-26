from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from mastery_engine import build_mastery_snapshot

OFFICIAL_AREAS = (
    "Matemática",
    "Física",
    "Química",
    "Lenguaje",
    "Estadística",
)

STATE_LABELS = {
    "unseen": "Sin evidencia",
    "initial": "Evidencia inicial",
    "weak": "Débil",
    "developing": "En desarrollo",
    "consolidating": "Consolidando",
    "mastered": "Dominado",
}


def _fold(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        char
        for char in text
        if not unicodedata.combining(char)
    )
    return " ".join(text.lower().strip().split())


def canonical_area(value: Any) -> str | None:
    folded = _fold(value)
    if folded.startswith("matem"):
        return "Matemática"
    if folded.startswith("fis"):
        return "Física"
    if folded.startswith("quim"):
        return "Química"
    if folded.startswith("leng"):
        return "Lenguaje"
    if folded.startswith("estad"):
        return "Estadística"
    return None


def _posterior(correct: int, attempts: int) -> float | None:
    if attempts <= 0:
        return None
    return round(
        (correct + 1.0) / (attempts + 2.0) * 100.0,
        1,
    )


def _state(score: float | None, attempts: int) -> str:
    if attempts <= 0 or score is None:
        return "unseen"
    if attempts < 2:
        return "initial"
    if score < 60.0:
        return "weak"
    if score < 75.0:
        return "developing"
    if score < 85.0 or attempts < 5:
        return "consolidating"
    return "mastered"


def _load_generated_coverage(project_root: Path) -> dict:
    path = (
        project_root
        / "results"
        / "p2e3_generation"
        / "publication"
        / "p2e3AB_generated_coverage_cumulative_final.json"
    )

    if not path.exists():
        return {
            "available": False,
            "published_generated_items": 0,
            "unique_topics_reinforced": 0,
            "by_area": [],
        }

    payload = json.loads(path.read_text(encoding="utf-8"))
    area_credit = Counter()
    area_topics = defaultdict(set)

    for row in payload.get("topics", []):
        area = canonical_area(row.get("area"))
        if area is None:
            continue

        topic_id = str(row.get("topic_id") or "").strip()
        credit = int(
            row.get("reinforcement_coverage_credit", 0) or 0
        )

        area_credit[area] += credit
        if topic_id:
            area_topics[area].add(topic_id)

    return {
        "available": True,
        "status": payload.get("status"),
        "published_generated_items": int(
            payload.get("published_generated_items", 0) or 0
        ),
        "unique_topics_reinforced": int(
            payload.get("unique_topics_reinforced", 0) or 0
        ),
        "direct_source_coverage_modified":
            payload.get("direct_source_coverage_modified"),
        "mastery_evidence_modified":
            payload.get("mastery_evidence_modified"),
        "by_area": [
            {
                "area": area,
                "generated_items": int(area_credit.get(area, 0)),
                "reinforced_topics":
                    len(area_topics.get(area, set())),
            }
            for area in OFFICIAL_AREAS
        ],
    }


def _area_rows(snapshot: dict) -> list[dict]:
    grouped = {
        area: {
            "attempts": 0,
            "correct": 0,
            "questions_attempted": 0,
        }
        for area in OFFICIAL_AREAS
    }

    for row in snapshot.get("questions", []):
        area = canonical_area(row.get("area"))
        if area is None:
            continue

        attempts = int(row.get("attempts", 0) or 0)
        correct = int(row.get("correct", 0) or 0)

        grouped[area]["attempts"] += attempts
        grouped[area]["correct"] += correct

        if attempts > 0:
            grouped[area]["questions_attempted"] += 1

    rows = []
    for area in OFFICIAL_AREAS:
        attempts = grouped[area]["attempts"]
        correct = grouped[area]["correct"]
        score = _posterior(correct, attempts)
        state = _state(score, attempts)

        rows.append({
            "area": area,
            "attempts": attempts,
            "correct": correct,
            "questions_attempted":
                grouped[area]["questions_attempted"],
            "mastery_score": score,
            "state": state,
            "state_label": STATE_LABELS[state],
            "has_evidence": attempts > 0,
        })

    return rows


def _weak_topics(snapshot: dict) -> list[dict]:
    grouped = {}

    for row in snapshot.get("questions", []):
        attempts = int(row.get("attempts", 0) or 0)
        if attempts <= 0:
            continue

        area = canonical_area(row.get("area"))
        if area is None:
            continue

        topic = str(row.get("topic") or "Sin tema").strip()
        key = (area, topic)

        entry = grouped.setdefault(
            key,
            {
                "area": area,
                "topic": topic,
                "attempts": 0,
                "correct": 0,
                "questions_attempted": 0,
            },
        )

        entry["attempts"] += attempts
        entry["correct"] += int(row.get("correct", 0) or 0)
        entry["questions_attempted"] += 1

    rows = []

    for entry in grouped.values():
        attempts = entry["attempts"]

        # Una sola observación no se presenta como debilidad.
        if attempts < 2:
            continue

        score = _posterior(entry["correct"], attempts)
        state = _state(score, attempts)

        if state not in {"weak", "developing"}:
            continue

        rows.append({
            **entry,
            "mastery_score": score,
            "state": state,
            "state_label": STATE_LABELS[state],
        })

    rows.sort(
        key=lambda row: (
            row["mastery_score"],
            -row["attempts"],
            row["area"],
            row["topic"],
        )
    )

    return rows[:20]


def build_dashboard_model(
    history: list[dict],
    runtime_bank: list[dict],
    project_root: Path | str | None = None,
) -> dict:
    if not isinstance(history, list):
        raise TypeError("history debe ser una lista")
    if not isinstance(runtime_bank, list):
        raise TypeError("runtime_bank debe ser una lista")

    project_root = (
        Path(project_root)
        if project_root is not None
        else Path(__file__).resolve().parent
    )

    snapshot = build_mastery_snapshot(history, runtime_bank)

    detailed = int(
        snapshot.get("detailed_attempt_records", 0) or 0
    )
    events = int(snapshot.get("evidence_events", 0) or 0)
    cold_start = detailed == 0 or events == 0

    return {
        "schema_version": "p2e4_dashboard_model_v1",
        "cold_start": cold_start,
        "learner": {
            "detailed_attempt_records": detailed,
            "legacy_summary_records_ignored": int(
                snapshot.get(
                    "legacy_summary_records_ignored",
                    0,
                )
                or 0
            ),
            "evidence_events": events,
            "eligible_question_count": int(
                snapshot.get("eligible_question_count", 0) or 0
            ),
            "attempted_eligible_question_count": int(
                snapshot.get(
                    "attempted_eligible_question_count",
                    0,
                )
                or 0
            ),
            "unseen_eligible_question_count": int(
                snapshot.get(
                    "unseen_eligible_question_count",
                    0,
                )
                or 0
            ),
            "question_coverage_pct": float(
                snapshot.get("coverage_pct", 0.0) or 0.0
            ),
        },
        "areas": _area_rows(snapshot),
        "weak_topics":
            [] if cold_start else _weak_topics(snapshot),
        "generated_coverage":
            _load_generated_coverage(project_root),
        "method": {
            "mastery_source":
                "mastery_engine.build_mastery_snapshot",
            "learner_evidence":
                "solo intentos detallados elegibles",
            "legacy_as_mastery": False,
            "generated_as_mastery": False,
            "source_review_as_mastery": False,
            "cold_start_policy":
                "neutral_without_invented_weaknesses",
            "coverage_vs_weakness_separated": True,
        },
    }


def _fmt_score(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}%"


def render_dashboard(st, pd, model: dict) -> None:
    st.title("📊 Dashboard de dominio")
    learner = model["learner"]

    if model["cold_start"]:
        st.info(
            "Todavía no hay intentos detallados suficientes para "
            "estimar fortalezas o debilidades. El estado se mantiene "
            "neutral hasta que completes nuevas evaluaciones o prácticas "
            "que guarden evidencia por pregunta."
        )

        legacy = learner["legacy_summary_records_ignored"]
        if legacy:
            st.caption(
                f"Se conservan {legacy} intentos históricos agregados, "
                "pero no se usan como evidencia de dominio."
            )

    columns = st.columns(4)
    columns[0].metric(
        "Intentos detallados",
        learner["detailed_attempt_records"],
    )
    columns[1].metric(
        "Eventos de evidencia",
        learner["evidence_events"],
    )
    columns[2].metric(
        "Preguntas con evidencia",
        (
            f"{learner['attempted_eligible_question_count']}"
            f"/{learner['eligible_question_count']}"
        ),
    )
    columns[3].metric(
        "Cobertura personal",
        f"{learner['question_coverage_pct']:.1f}%",
    )

    st.subheader("Dominio por área FICA")

    area_rows = [
        {
            "Área": row["area"],
            "Intentos": row["attempts"],
            "Correctas": row["correct"],
            "Preguntas con evidencia":
                row["questions_attempted"],
            "Mastery": _fmt_score(row["mastery_score"]),
            "Estado": row["state_label"],
        }
        for row in model["areas"]
    ]

    st.dataframe(
        pd.DataFrame(area_rows),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Temas que requieren atención")

    if model["cold_start"]:
        st.caption(
            "Aún no se muestran temas débiles: no existe "
            "evidencia detallada suficiente."
        )
    elif not model["weak_topics"]:
        st.success(
            "Con la evidencia disponible no hay temas "
            "clasificados como débiles o en desarrollo."
        )
    else:
        weak_rows = [
            {
                "Área": row["area"],
                "Tema": row["topic"],
                "Intentos": row["attempts"],
                "Correctas": row["correct"],
                "Mastery":
                    _fmt_score(row["mastery_score"]),
                "Estado": row["state_label"],
            }
            for row in model["weak_topics"]
        ]

        st.dataframe(
            pd.DataFrame(weak_rows),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Contexto de cobertura curricular")
    generated = model["generated_coverage"]

    if generated["available"]:
        c1, c2 = st.columns(2)
        c1.metric(
            "Refuerzos generados validados",
            generated["published_generated_items"],
        )
        c2.metric(
            "Temas reforzados",
            generated["unique_topics_reinforced"],
        )

        st.caption(
            "Esta cobertura describe disponibilidad de material de "
            "práctica. No aumenta tu mastery y no convierte las preguntas "
            "generadas en contenido oficial UTN."
        )

        coverage_rows = [
            {
                "Área": row["area"],
                "Refuerzos generados":
                    row["generated_items"],
                "Temas reforzados":
                    row["reinforced_topics"],
            }
            for row in generated["by_area"]
        ]

        st.dataframe(
            pd.DataFrame(coverage_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption(
            "El overlay de cobertura generada no está disponible."
        )

    with st.expander("Cómo leer este dashboard"):
        st.markdown(
            "- **Mastery** usa únicamente evidencia detallada de preguntas elegibles.\n"
            "- Los intentos históricos agregados se conservan, pero no se convierten retroactivamente en mastery.\n"
            "- El refuerzo generado aparece como **cobertura de práctica**, no como evidencia del estudiante.\n"
            "- Un tema no se etiqueta como débil con una sola observación.\n"
            "- Las cinco áreas mostradas corresponden al marco FICA usado por el simulador."
        )


def self_test() -> dict:
    runtime = [
        {
            "id": "T-MAT-1",
            "subject": "Matemática",
            "topic": "Álgebra",
            "skill": "Resolver",
            "subskill": "Lineales",
            "quality": {
                "mastery_eligible": True,
                "status": "validated",
            },
        },
        {
            "id": "T-FIS-1",
            "subject": "Física",
            "topic": "Fuerzas",
            "skill": "Aplicar",
            "subskill": "Newton",
            "quality": {
                "mastery_eligible": True,
                "status": "validated",
            },
        },
        {
            "id": "T-R-1",
            "subject": "Química",
            "topic": "Refuerzo",
            "origin": "generated_reinforcement",
            "quality": {
                "mastery_eligible": True,
                "status": "validated",
            },
        },
    ]

    cold = build_dashboard_model(
        [],
        runtime,
        Path(__file__).resolve().parent,
    )

    if not cold["cold_start"]:
        raise AssertionError("cold start debe ser neutral")
    if cold["weak_topics"]:
        raise AssertionError(
            "cold start no debe inventar debilidades"
        )

    history = [
        {
            "fecha": "2026-01-01T10:00:00",
            "mode": "Práctica",
            "titulo": "Test 1",
            "items": [
                {
                    "question_id": "T-MAT-1",
                    "answered": True,
                    "correct": False,
                }
            ],
        },
        {
            "fecha": "2026-01-02T10:00:00",
            "mode": "Práctica",
            "titulo": "Test 2",
            "items": [
                {
                    "question_id": "T-MAT-1",
                    "answered": True,
                    "correct": False,
                },
                {
                    "question_id": "T-R-1",
                    "answered": True,
                    "correct": True,
                },
            ],
        },
    ]

    model = build_dashboard_model(
        history,
        runtime,
        Path(__file__).resolve().parent,
    )

    if model["cold_start"]:
        raise AssertionError(
            "con evidencia detallada no debe ser cold start"
        )

    if model["learner"]["evidence_events"] != 2:
        raise AssertionError(
            "refuerzo generado no debe entrar en mastery"
        )

    if not model["weak_topics"]:
        raise AssertionError(
            "dos errores de álgebra deben generar señal débil"
        )

    return {
        "cold_start_neutral": True,
        "weakness_requires_evidence": True,
        "generated_excluded_from_mastery": True,
        "official_area_rows": len(model["areas"]) == 5,
    }


if __name__ == "__main__":
    checks = self_test()
    print("P2-E4 dashboard_engine self-test")
    for name, passed in checks.items():
        if not passed:
            raise SystemExit(f"FAIL: {name}")
        print(f"[OK] {name}")

