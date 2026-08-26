from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any


SNAPSHOT_SCHEMA_VERSION = "p2e1_mastery_snapshot_v1"
ATTEMPT_SCHEMA_VERSION = "p2e1_attempt_v1"

PRIOR_CORRECT = 1.0
PRIOR_INCORRECT = 1.0


def _text(value: Any, fallback: str = "") -> str:
    value = "" if value is None else str(value)
    value = value.strip()
    return value or fallback


def is_mastery_eligible_question(question: dict) -> bool:
    if not isinstance(question, dict):
        return False

    quality = question.get("quality")
    if not isinstance(quality, dict):
        return False

    if quality.get("mastery_eligible") is not True:
        return False

    if quality.get("status") not in {None, "validated"}:
        return False

    if question.get("status") == "revisar_fuente":
        return False

    if question.get("taxonomy_status") == "source_review":
        return False

    origin = _text(question.get("origin")).lower()
    content_origin = _text(question.get("content_origin")).lower()

    if origin == "generated_reinforcement":
        return False

    if content_origin == "generated_reinforcement":
        return False

    return bool(_text(question.get("id")))


def eligible_question_map(runtime_bank: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}

    for question in runtime_bank:
        if not is_mastery_eligible_question(question):
            continue

        question_id = _text(question.get("id"))

        if question_id in out:
            raise ValueError(f"duplicate eligible question id: {question_id}")

        out[question_id] = question

    return out


def _question_metadata(question: dict) -> dict:
    return {
        "question_id": _text(question.get("id")),
        "simulacro": _text(question.get("simulacro"), "Sin fuente"),
        "area": _text(
            question.get("subject") or question.get("area"),
            "Sin área",
        ),
        "topic": _text(question.get("topic"), "Sin tema"),
        "skill": _text(question.get("skill"), "Sin habilidad"),
        "subskill": _text(question.get("subskill"), "Sin subhabilidad"),
        "content_origin": _text(
            question.get("content_origin"),
            "legacy",
        ),
        "content_package_id": _text(
            question.get("content_package_id"),
            "legacy",
        ),
    }


def _posterior_score(correct: int, attempts: int) -> float | None:
    if attempts <= 0:
        return None

    posterior = (
        correct + PRIOR_CORRECT
    ) / (
        attempts + PRIOR_CORRECT + PRIOR_INCORRECT
    )

    return round(posterior * 100.0, 1)


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


def _summary(
    events: list[dict],
    metadata: dict | None = None,
) -> dict:
    attempts = len(events)
    correct = sum(1 for event in events if event["correct"])
    unanswered = sum(1 for event in events if not event["answered"])
    incorrect = attempts - correct
    score = _posterior_score(correct, attempts)

    last_seen = None
    if events:
        last_seen = max(
            (
                event.get("attempt_date")
                for event in events
                if event.get("attempt_date")
            ),
            default=None,
        )

    result = {
        "attempts": attempts,
        "correct": correct,
        "incorrect": incorrect,
        "unanswered": unanswered,
        "raw_accuracy_pct": (
            round(correct / attempts * 100.0, 1)
            if attempts
            else None
        ),
        "mastery_score": score,
        "state": _state(score, attempts),
        "last_seen": last_seen,
    }

    if metadata:
        result = dict(metadata) | result

    return result


def _group_rows(
    events: list[dict],
    fields: tuple[str, ...],
) -> list[dict]:
    groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)

    for event in events:
        key = tuple(event[field] for field in fields)
        groups[key].append(event)

    rows = []

    for key, grouped_events in groups.items():
        meta = {
            field: value
            for field, value in zip(fields, key)
        }
        rows.append(_summary(grouped_events, meta))

    rows.sort(
        key=lambda row: (
            row["mastery_score"]
            if row["mastery_score"] is not None
            else 101.0,
            -row["attempts"],
            tuple(str(row[field]) for field in fields),
        )
    )

    return rows


def build_mastery_snapshot(
    history: list[dict],
    runtime_bank: list[dict],
) -> dict:
    eligible = eligible_question_map(runtime_bank)

    question_events: dict[str, list[dict]] = {
        question_id: []
        for question_id in eligible
    }

    events: list[dict] = []
    detailed_attempt_records = 0
    legacy_summary_records_ignored = 0
    malformed_item_count = 0
    ineligible_item_count = 0

    for record in history:
        if not isinstance(record, dict):
            legacy_summary_records_ignored += 1
            continue

        items = record.get("items")

        if not isinstance(items, list):
            legacy_summary_records_ignored += 1
            continue

        detailed_attempt_records += 1
        attempt_date = _text(record.get("fecha"))
        mode = _text(record.get("mode"), "Desconocido")
        title = _text(record.get("titulo"), "Intento")

        for item in items:
            if not isinstance(item, dict):
                malformed_item_count += 1
                continue

            question_id = _text(
                item.get("question_id") or item.get("id")
            )

            if not question_id:
                malformed_item_count += 1
                continue

            question = eligible.get(question_id)

            if question is None:
                ineligible_item_count += 1
                continue

            metadata = _question_metadata(question)

            event = {
                **metadata,
                "attempt_date": attempt_date,
                "mode": mode,
                "title": title,
                "answered": bool(item.get("answered")),
                "correct": bool(item.get("correct")),
            }

            events.append(event)
            question_events[question_id].append(event)

    question_rows = []

    for question_id, question in sorted(eligible.items()):
        metadata = _question_metadata(question)
        question_rows.append(
            _summary(
                question_events[question_id],
                metadata,
            )
        )

    attempted_ids = {
        row["question_id"]
        for row in question_rows
        if row["attempts"] > 0
    }

    unseen_ids = [
        row["question_id"]
        for row in question_rows
        if row["state"] == "unseen"
    ]

    by_area = _group_rows(events, ("area",))
    by_topic = _group_rows(events, ("area", "topic"))
    by_skill = _group_rows(events, ("area", "topic", "skill"))
    by_subskill = _group_rows(
        events,
        ("area", "topic", "skill", "subskill"),
    )

    weakest_subskills = [
        row
        for row in by_subskill
        if row["attempts"] > 0
    ][:20]

    state_counts = defaultdict(int)
    for row in question_rows:
        state_counts[row["state"]] += 1

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "method": {
            "evidence": (
                "Solo intentos detallados sobre preguntas runtime "
                "con quality.mastery_eligible=True; source_review y "
                "refuerzo quedan excluidos."
            ),
            "posterior": (
                "Beta(1,1): mastery_score = "
                "(correctas+1)/(intentos+2)*100."
            ),
            "states": {
                "unseen": "0 intentos",
                "initial": "1 intento",
                "weak": ">=2 intentos y score < 60",
                "developing": "score 60-74.9",
                "consolidating": (
                    "score 75-84.9 o menos de 5 intentos"
                ),
                "mastered": "score >=85 y al menos 5 intentos",
            },
        },
        "runtime_question_count": len(runtime_bank),
        "eligible_question_count": len(eligible),
        "attempted_eligible_question_count": len(attempted_ids),
        "unseen_eligible_question_count": len(unseen_ids),
        "coverage_pct": round(
            len(attempted_ids) / len(eligible) * 100.0,
            1,
        ) if eligible else 0.0,
        "detailed_attempt_records": detailed_attempt_records,
        "legacy_summary_records_ignored": legacy_summary_records_ignored,
        "evidence_events": len(events),
        "malformed_item_count": malformed_item_count,
        "ineligible_item_count": ineligible_item_count,
        "question_state_counts": dict(sorted(state_counts.items())),
        "questions": question_rows,
        "by_area": by_area,
        "by_topic": by_topic,
        "by_skill": by_skill,
        "by_subskill": by_subskill,
        "weakest_subskills": weakest_subskills,
        "unseen_question_ids": unseen_ids,
    }

