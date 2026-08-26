from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
import hashlib
import json
import os

from recommendations_engine import build_recommendations_model


SCHEMA_VERSION = "p2e7_student_state_v1"
STATE_FILENAME = "student_state.json"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def history_fingerprint(
    history: list[dict[str, Any]] | None,
) -> str:
    history = history or []

    raw = _canonical_json(history).encode("utf-8")

    return hashlib.sha256(raw).hexdigest().upper()


def _valid_state(
    state: Any,
    fingerprint: str | None = None,
) -> bool:

    if not isinstance(state, dict):
        return False

    if state.get("schema_version") != SCHEMA_VERSION:
        return False

    if not isinstance(
        state.get("recommendations_model"),
        dict,
    ):
        return False

    if fingerprint is not None:
        if state.get("history_fingerprint") != fingerprint:
            return False

    return True


def _read_state(path: Path) -> dict[str, Any] | None:

    if not path.exists():
        return None

    try:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return None

    if not _valid_state(data):
        return None

    return data


def _atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = path.with_name(
        path.name + ".tmp"
    )

    temp.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    os.replace(
        temp,
        path,
    )


def build_persistent_state(
    history: list[dict[str, Any]] | None,
) -> dict[str, Any]:

    history = history or []

    # Trabajamos con copia para garantizar
    # que persistencia nunca modifique history.
    safe_history = deepcopy(history)

    fingerprint = history_fingerprint(
        safe_history
    )

    detailed = [
        record
        for record in safe_history
        if isinstance(record, dict)
        and record.get("schema_version")
        == "p2e1_attempt_v1"
    ]

    legacy = [
        record
        for record in safe_history
        if isinstance(record, dict)
        and record.get("schema_version")
        != "p2e1_attempt_v1"
    ]

    strategies = Counter(
        str(
            record.get(
                "selection_strategy",
                "unknown",
            )
        )
        for record in detailed
    )

    dates = [
        str(record.get("fecha"))
        for record in safe_history
        if isinstance(record, dict)
        and record.get("fecha")
    ]

    recommendation_model = (
        build_recommendations_model(
            safe_history
        )
    )

    return {
        "schema_version":
            SCHEMA_VERSION,

        "generated_at":
            datetime.now().isoformat(
                timespec="seconds"
            ),

        "history_fingerprint":
            fingerprint,

        "history_records":
            len(safe_history),

        "legacy_records":
            len(legacy),

        "detailed_attempts":
            len(detailed),

        "last_attempt_date":
            max(dates) if dates else None,

        "selection_strategies":
            dict(strategies),

        "summary": {
            "evaluated_items":
                recommendation_model.get(
                    "evaluated_items",
                    0,
                ),

            "topics":
                recommendation_model.get(
                    "topics",
                    0,
                ),

            "high_priority":
                recommendation_model.get(
                    "high_count",
                    0,
                ),

            "medium_priority":
                recommendation_model.get(
                    "medium_count",
                    0,
                ),

            "maintain":
                recommendation_model.get(
                    "maintain_count",
                    0,
                ),

            "cold_start":
                recommendation_model.get(
                    "cold_start",
                    True,
                ),
        },

        "recommendations_model":
            recommendation_model,
    }


def sync_persistent_state(
    history: list[dict[str, Any]] | None,
    project_root: str | Path,
) -> dict[str, Any]:

    root = Path(project_root)

    state_path = (
        root
        / "results"
        / STATE_FILENAME
    )

    fingerprint = history_fingerprint(
        history or []
    )

    existing = _read_state(
        state_path
    )

    if (
        existing is not None
        and existing.get(
            "history_fingerprint"
        ) == fingerprint
    ):
        result = deepcopy(existing)
        result["_sync_status"] = "reused"
        return result

    new_state = build_persistent_state(
        history
    )

    _atomic_write_json(
        state_path,
        new_state,
    )

    result = deepcopy(new_state)
    result["_sync_status"] = "rebuilt"

    return result


def load_persistent_state(
    project_root: str | Path,
) -> dict[str, Any] | None:

    path = (
        Path(project_root)
        / "results"
        / STATE_FILENAME
    )

    return _read_state(path)
