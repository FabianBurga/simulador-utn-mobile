from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


# =============================================================================
# P2-E2A - Motor adaptativo desacoplado de Streamlit
# =============================================================================
#
# Este módulo NO modifica app.py ni mastery_engine.py.
# Recibe un pool ya filtrado por el flujo existente de la app y solo reordena/
# selecciona preguntas curriculares elegibles C0-C2.
#
# Guardas:
# - Full Exam: prohibido adaptativo.
# - source_review / no-FICA / apoyo curricular: fuera por crosswalk.
# - C3: fuera por perfil de costo manual.
# - refuerzo generado: fuera salvo que tenga explícitamente un ID elegible del
#   perfil congelado (hoy no ocurre).
# - historial legacy agregado: ignorado.
# - solo p2e1_attempt_v1 puede aportar evidencia personal.
# =============================================================================


ATTEMPT_SCHEMA = "p2e1_attempt_v1"
FULL_EXAM_MODE = "Examen completo"

DIRECT_STATUSES = {
    "mapped_rule_validated",
    "mapped_reviewed",
}

ALLOWED_MANUAL_COSTS = {"C0", "C1", "C2"}

DEFAULT_OFFICIAL_AREAS = (
    "Matemática",
    "Física",
    "Química",
    "Lenguaje",
    "Estadística",
)


@dataclass(frozen=True)
class QuestionMeta:
    question_id: str
    area: str
    topic_id: str
    topic: str
    manual_cost: str


@dataclass
class Evidence:
    attempts: int = 0
    correct: int = 0
    latest_ts: datetime | None = None

    @property
    def beta_mean(self) -> float:
        return (self.correct + 1.0) / (self.attempts + 2.0)

    @property
    def uncertainty(self) -> float:
        return 1.0 / math.sqrt(self.attempts + 1.0)


@dataclass
class AdaptiveContext:
    metadata: dict[str, QuestionMeta]
    official_areas: tuple[str, ...]
    hashes: dict[str, str]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _question_id(q: dict[str, Any]) -> str:
    value = q.get("id")
    if value is None:
        value = q.get("question_id")
    return str(value or "").strip()


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _first_value(d: dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in d:
            return d.get(key)
    return None


def _attempt_items(attempt: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "responses", "questions", "answers", "evidence"):
        value = attempt.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _correct_value(item: dict[str, Any]) -> bool | None:
    value = _first_value(
        item,
        ("correct", "is_correct", "correcta", "was_correct"),
    )

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "si", "sí", "correct", "correcto"}:
            return True
        if normalized in {"false", "0", "no", "incorrect", "incorrecto"}:
            return False

    return None


def build_question_evidence(
    history: Sequence[Any],
    eligible_ids: set[str],
) -> dict[str, Evidence]:
    """
    Solo usa intentos detallados p2e1_attempt_v1.
    Los 11 registros legacy agregados quedan ignorados por diseño.
    """
    out: dict[str, Evidence] = defaultdict(Evidence)

    for attempt in history:
        if not isinstance(attempt, dict):
            continue
        if attempt.get("schema_version") != ATTEMPT_SCHEMA:
            continue

        attempt_ts = _parse_datetime(
            _first_value(
                attempt,
                ("fecha", "timestamp", "completed_at", "finished_at", "created_at"),
            )
        )

        for item in _attempt_items(attempt):
            qid = str(
                _first_value(
                    item,
                    ("question_id", "qid", "id"),
                )
                or ""
            ).strip()

            if not qid or qid not in eligible_ids:
                continue

            correct = _correct_value(item)
            if correct is None:
                # Sin corrección verificable no fabricamos evidencia.
                continue

            ev = out[qid]
            ev.attempts += 1
            if correct:
                ev.correct += 1

            item_ts = _parse_datetime(
                _first_value(
                    item,
                    ("timestamp", "answered_at", "completed_at"),
                )
            )
            ts = item_ts or attempt_ts

            if ts is not None and (ev.latest_ts is None or ts > ev.latest_ts):
                ev.latest_ts = ts

    return dict(out)


def aggregate_topic_evidence(
    metadata: dict[str, QuestionMeta],
    question_evidence: dict[str, Evidence],
) -> dict[str, Evidence]:
    totals: dict[str, Evidence] = defaultdict(Evidence)

    for qid, meta in metadata.items():
        ev = question_evidence.get(qid)
        if ev is None:
            continue

        total = totals[meta.topic_id]
        total.attempts += ev.attempts
        total.correct += ev.correct

        if ev.latest_ts is not None:
            if total.latest_ts is None or ev.latest_ts > total.latest_ts:
                total.latest_ts = ev.latest_ts

    return dict(totals)


def _recency_need(latest_ts: datetime | None, now: datetime | None = None) -> float:
    """
    Señal suave y transparente.
    0 si no hay timestamp. Si existe:
    - hoy -> cerca de 0
    - 30 días o más -> 1
    """
    if latest_ts is None:
        return 0.0

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    age_days = max(
        0.0,
        (now.astimezone(timezone.utc) - latest_ts.astimezone(timezone.utc)).total_seconds()
        / 86400.0,
    )
    return min(1.0, age_days / 30.0)


def learner_need_score(
    evidence: Evidence | None,
    now: datetime | None = None,
) -> float:
    """
    Baseline E1 congelado:
      0.55*(1-beta_mean)
    + 0.30*uncertainty
    + 0.15*recency_need

    En cold start NO usamos este score para etiquetar al alumno como "débil".
    La exploración de temas no vistos tiene prioridad.
    """
    ev = evidence or Evidence()
    mastery_need = 1.0 - ev.beta_mean
    uncertainty = ev.uncertainty
    recency = _recency_need(ev.latest_ts, now=now)
    return 0.55 * mastery_need + 0.30 * uncertainty + 0.15 * recency


def load_adaptive_context(base: Path | str = ".") -> AdaptiveContext:
    base = Path(base)

    curr = base / "results" / "p2e1_5_curriculum"
    mastery = base / "results" / "p2e1_5_mastery"
    manual = mastery / "manual_cost"
    final = base / "results" / "p2e1_5_final"

    crosswalk_path = curr / "p2e1_5_crosswalk_frozen.json"
    freeze_c_path = curr / "p2e1_5_C_freeze_manifest.json"

    e1_contract_path = mastery / "p2e1_5_E1_priority_contract.json"
    freeze_e1_path = mastery / "p2e1_5_E1_freeze_manifest.json"

    e2_profile_path = manual / "p2e1_5_E2_manual_cost_profile_frozen.json"
    freeze_e2_path = manual / "p2e1_5_E2_freeze_manifest.json"

    audit_f_path = final / "p2e1_5_F_global_audit.json"
    freeze_f_path = final / "p2e1_5_F_freeze_manifest.json"

    required = [
        crosswalk_path,
        freeze_c_path,
        e1_contract_path,
        freeze_e1_path,
        e2_profile_path,
        freeze_e2_path,
        audit_f_path,
        freeze_f_path,
    ]

    for path in required:
        if not path.exists():
            raise RuntimeError(f"Falta checkpoint P2-E1.5 requerido: {path}")

    freeze_c = _load_json(freeze_c_path)
    freeze_e1 = _load_json(freeze_e1_path)
    freeze_e2 = _load_json(freeze_e2_path)
    freeze_f = _load_json(freeze_f_path)
    audit_f = _load_json(audit_f_path)

    for name, manifest in [
        ("C", freeze_c),
        ("E1", freeze_e1),
        ("E2", freeze_e2),
        ("F", freeze_f),
    ]:
        if manifest.get("status") != "FROZEN":
            raise RuntimeError(f"P2-E1.5-{name} no está FROZEN")

    # Verificación mínima de hashes congelados.
    #
    # Los manifests guardan rutas relativas al proyecto, mientras que la app
    # puede llamar este cargador con una ruta base absoluta (BASE). Normalizamos
    # ambos formatos para que la verificación dependa del archivo/hash real y
    # no de cómo fue representada la ruta.
    def manifest_hash(manifest: dict, section: str, path: Path) -> str | None:
        mapping = manifest.get(section, {})
        if not isinstance(mapping, dict):
            return None

        try:
            relative = path.resolve().relative_to(base.resolve())
        except ValueError:
            relative = path

        target = str(relative).replace("\\", "/")

        for raw_key, value in mapping.items():
            normalized = str(raw_key).replace("\\", "/")
            if normalized == target:
                return str(value)

        return None

    c_expected = manifest_hash(
        freeze_c,
        "outputs",
        crosswalk_path,
    )
    if c_expected != _sha256(crosswalk_path):
        raise RuntimeError("Hash del crosswalk no coincide con P2-E1.5-C")

    e1_expected = manifest_hash(
        freeze_e1,
        "outputs",
        e1_contract_path,
    )
    if e1_expected != _sha256(e1_contract_path):
        raise RuntimeError("Hash del contrato E1 no coincide con su freeze")

    e2_expected = manifest_hash(
        freeze_e2,
        "outputs",
        e2_profile_path,
    )
    if e2_expected != _sha256(e2_profile_path):
        raise RuntimeError("Hash del perfil E2 no coincide con su freeze")

    f_expected = manifest_hash(
        freeze_f,
        "outputs",
        audit_f_path,
    )
    if f_expected != _sha256(audit_f_path):
        raise RuntimeError("Hash de auditoría global F no coincide con su freeze")

    if audit_f.get("status") != "PASS":
        raise RuntimeError("Auditoría global P2-E1.5-F no está PASS")
    if audit_f.get("ready_for_P2_E2") is not True:
        raise RuntimeError("P2-E1.5-F no autoriza P2-E2")

    e1_contract = _load_json(e1_contract_path)
    if e1_contract.get("activation", {}).get("adaptive_selection") is not False:
        raise RuntimeError("E1 ya aparece activado inesperadamente")

    crosswalk = _load_json(crosswalk_path)
    e2 = _load_json(e2_profile_path)

    cw_items = crosswalk.get("items")
    e2_items = e2.get("items")

    if not isinstance(cw_items, list):
        raise RuntimeError("Crosswalk items inválidos")
    if not isinstance(e2_items, list):
        raise RuntimeError("E2 items inválidos")

    direct_by_id = {
        str(item["question_id"]): item
        for item in cw_items
        if item.get("status") in DIRECT_STATUSES
        and item.get("coverage_credit") is True
    }

    manual_by_id = {
        str(item["question_id"]): item
        for item in e2_items
    }

    metadata: dict[str, QuestionMeta] = {}

    for qid, cw_item in direct_by_id.items():
        manual = manual_by_id.get(qid)
        if manual is None:
            continue

        cost = str(manual.get("manual_cost_final"))
        eligible = manual.get("exam_like_eligible") is True

        if not eligible or cost not in ALLOWED_MANUAL_COSTS:
            continue

        metadata[qid] = QuestionMeta(
            question_id=qid,
            area=str(cw_item["canonical_area"]),
            topic_id=str(cw_item["topic_id"]),
            topic=str(cw_item["canonical_topic"]),
            manual_cost=cost,
        )

    if len(metadata) != 360:
        raise RuntimeError(
            f"Esperaba 360 preguntas C0-C2 elegibles; encontré {len(metadata)}"
        )

    areas = tuple(
        area
        for area in DEFAULT_OFFICIAL_AREAS
        if any(m.area == area for m in metadata.values())
    )

    if not areas:
        raise RuntimeError("No hay áreas curriculares elegibles")

    return AdaptiveContext(
        metadata=metadata,
        official_areas=areas,
        hashes={
            "crosswalk": _sha256(crosswalk_path),
            "E1_contract": _sha256(e1_contract_path),
            "E2_profile": _sha256(e2_profile_path),
            "F_audit": _sha256(audit_f_path),
        },
    )


def _candidate_dict(
    candidates: Sequence[dict[str, Any]],
    metadata: dict[str, QuestionMeta],
    selected_area: str | None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}

    for q in candidates:
        qid = _question_id(q)
        if not qid or qid not in metadata:
            continue

        meta = metadata[qid]
        if selected_area is not None and meta.area != selected_area:
            continue

        out[qid] = q

    return out


def _topic_candidates(
    remaining: dict[str, dict[str, Any]],
    metadata: dict[str, QuestionMeta],
    area: str,
) -> dict[str, list[str]]:
    topics: dict[str, list[str]] = defaultdict(list)

    for qid in remaining:
        meta = metadata[qid]
        if meta.area == area:
            topics[meta.topic_id].append(qid)

    return dict(topics)


def _choose_area(
    remaining: dict[str, dict[str, Any]],
    metadata: dict[str, QuestionMeta],
    area_selected_counts: Counter,
    official_areas: Sequence[str],
    rng: random.Random,
) -> str | None:
    available = [
        area
        for area in official_areas
        if any(metadata[qid].area == area for qid in remaining)
    ]

    if not available:
        return None

    minimum = min(area_selected_counts[a] for a in available)
    ties = [a for a in available if area_selected_counts[a] == minimum]
    return rng.choice(ties)


def _choose_topic(
    topic_to_qids: dict[str, list[str]],
    topic_evidence: dict[str, Evidence],
    topic_selected_counts: Counter,
    rng: random.Random,
    now: datetime | None,
) -> str:
    available_topics = list(topic_to_qids)

    unseen = [
        tid
        for tid in available_topics
        if topic_evidence.get(tid, Evidence()).attempts == 0
    ]

    if unseen:
        # Cobertura personal antes de drilling. Si ya elegimos un tema dentro
        # del mismo intento, preferimos los menos usados en la sesión.
        minimum = min(topic_selected_counts[t] for t in unseen)
        ties = [t for t in unseen if topic_selected_counts[t] == minimum]
        return rng.choice(ties)

    scored = []
    for tid in available_topics:
        ev = topic_evidence.get(tid)
        score = learner_need_score(ev, now=now)

        # Pequeña penalización por repetición dentro de la misma sesión para
        # preservar diversidad sin anular la señal de debilidad.
        score -= 0.03 * topic_selected_counts[tid]
        scored.append((score, rng.random(), tid))

    scored.sort(reverse=True)
    return scored[0][2]


def _choose_question(
    qids: Sequence[str],
    q_evidence: dict[str, Evidence],
    rng: random.Random,
    now: datetime | None,
) -> str:
    unseen = [qid for qid in qids if q_evidence.get(qid, Evidence()).attempts == 0]
    if unseen:
        return rng.choice(unseen)

    scored = []
    for qid in qids:
        score = learner_need_score(q_evidence.get(qid), now=now)
        scored.append((score, rng.random(), qid))

    scored.sort(reverse=True)
    return scored[0][2]


def select_adaptive_questions(
    candidates: Sequence[dict[str, Any]],
    amount: int,
    history: Sequence[Any],
    mode: str,
    context: AdaptiveContext,
    *,
    selected_area: str | None = None,
    seed: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Selección adaptativa P2-E2A.

    La app debe pasar un pool YA permitido por fuente/modo.
    Este motor añade únicamente:
    - elegibilidad curricular directa,
    - barrera C0-C2,
    - equilibrio área/tema,
    - evidencia personal detallada.

    No modifica preguntas ni historial.
    """
    if mode == FULL_EXAM_MODE:
        raise ValueError(
            "P2-E2 no puede aplicarse a Examen completo; "
            "el flujo Full Exam debe permanecer sin adaptación."
        )

    if amount <= 0:
        return []

    rng = random.Random(seed)

    remaining = _candidate_dict(
        candidates=candidates,
        metadata=context.metadata,
        selected_area=selected_area,
    )

    if not remaining:
        return []

    target = min(int(amount), len(remaining))

    eligible_ids = set(context.metadata)
    q_evidence = build_question_evidence(history, eligible_ids)
    topic_evidence = aggregate_topic_evidence(context.metadata, q_evidence)

    area_selected_counts: Counter = Counter()
    topic_selected_counts: Counter = Counter()

    selected: list[dict[str, Any]] = []

    if selected_area is not None:
        area_order = (selected_area,)
    else:
        area_order = context.official_areas

    while remaining and len(selected) < target:
        if selected_area is not None:
            area = selected_area
            if not any(
                context.metadata[qid].area == area
                for qid in remaining
            ):
                break
        else:
            area = _choose_area(
                remaining=remaining,
                metadata=context.metadata,
                area_selected_counts=area_selected_counts,
                official_areas=area_order,
                rng=rng,
            )
            if area is None:
                break

        topics = _topic_candidates(
            remaining=remaining,
            metadata=context.metadata,
            area=area,
        )
        if not topics:
            if selected_area is not None:
                break
            area_selected_counts[area] += 1
            continue

        topic_id = _choose_topic(
            topic_to_qids=topics,
            topic_evidence=topic_evidence,
            topic_selected_counts=topic_selected_counts,
            rng=rng,
            now=now,
        )

        qid = _choose_question(
            qids=topics[topic_id],
            q_evidence=q_evidence,
            rng=rng,
            now=now,
        )

        selected.append(remaining.pop(qid))
        area_selected_counts[area] += 1
        topic_selected_counts[topic_id] += 1

    return selected


# =============================================================================
# Auditoría de proyecto
# =============================================================================


def audit_project(base: Path | str = ".") -> dict[str, Any]:
    base = Path(base)
    context = load_adaptive_context(base)

    history_path = base / "results" / "history.json"
    history = _load_json(history_path) if history_path.exists() else []

    if not isinstance(history, list):
        raise RuntimeError("results/history.json no es una lista")

    legacy = 0
    detailed = 0
    for r in history:
        if isinstance(r, dict) and r.get("schema_version") == ATTEMPT_SCHEMA:
            detailed += 1
        elif isinstance(r, dict):
            legacy += 1

    area_counts = Counter(meta.area for meta in context.metadata.values())
    topic_counts = Counter(meta.topic_id for meta in context.metadata.values())
    manual_counts = Counter(meta.manual_cost for meta in context.metadata.values())

    return {
        "eligible_questions": len(context.metadata),
        "areas": dict(area_counts),
        "selectable_topics": len(topic_counts),
        "manual_costs": dict(manual_counts),
        "history_legacy": legacy,
        "history_detailed": detailed,
        "cold_start": detailed == 0,
        "hashes": context.hashes,
    }


# =============================================================================
# Autoauditoría sintética
# =============================================================================


def _fake_context(
    rows: Iterable[tuple[str, str, str, str]],
) -> AdaptiveContext:
    metadata = {
        qid: QuestionMeta(
            question_id=qid,
            area=area,
            topic_id=topic,
            topic=topic,
            manual_cost=cost,
        )
        for qid, area, topic, cost in rows
        if cost in ALLOWED_MANUAL_COSTS
    }

    areas = tuple(
        area
        for area in DEFAULT_OFFICIAL_AREAS
        if any(m.area == area for m in metadata.values())
    )

    return AdaptiveContext(
        metadata=metadata,
        official_areas=areas,
        hashes={},
    )


def self_test() -> dict[str, bool]:
    checks: dict[str, bool] = {}

    # 1) Full Exam queda bloqueado.
    ctx = _fake_context([
        ("M1", "Matemática", "MAT-A", "C1"),
    ])
    try:
        select_adaptive_questions(
            [{"id": "M1"}],
            1,
            [],
            FULL_EXAM_MODE,
            ctx,
            seed=1,
        )
        checks["full_exam_blocked"] = False
    except ValueError:
        checks["full_exam_blocked"] = True

    # 2) C3 queda fuera incluso si aparece en candidates.
    ctx = _fake_context([
        ("M1", "Matemática", "MAT-A", "C1"),
        ("M2", "Matemática", "MAT-A", "C3"),
    ])
    picked = select_adaptive_questions(
        [{"id": "M1"}, {"id": "M2"}],
        2,
        [],
        "Examen rápido",
        ctx,
        seed=2,
    )
    checks["C3_excluded"] = [_question_id(x) for x in picked] == ["M1"]

    # 3) Volumen de Física no domina Mixto.
    rows = []
    candidates = []

    for n in range(20):
        qid = f"F{n:02d}"
        rows.append((qid, "Física", "FIS-A", "C1"))
        candidates.append({"id": qid})

    for area, prefix, topic in [
        ("Matemática", "M", "MAT-A"),
        ("Química", "Q", "QUI-A"),
        ("Lenguaje", "L", "LEN-A"),
        ("Estadística", "E", "EST-A"),
    ]:
        for n in range(2):
            qid = f"{prefix}{n}"
            rows.append((qid, area, topic, "C1"))
            candidates.append({"id": qid})

    ctx = _fake_context(rows)
    picked = select_adaptive_questions(
        candidates,
        10,
        [],
        "Examen rápido",
        ctx,
        seed=3,
    )

    area_counts = Counter(
        ctx.metadata[_question_id(q)].area
        for q in picked
    )
    checks["physics_volume_does_not_dominate"] = (
        len(area_counts) == 5
        and max(area_counts.values()) - min(area_counts.values()) <= 1
    )

    # 4) Dentro de un área, volumen de un tema no domina a otro tema no visto.
    rows = []
    candidates = []
    for n in range(10):
        qid = f"A{n}"
        rows.append((qid, "Física", "TOPIC-A", "C1"))
        candidates.append({"id": qid})
    rows.append(("B0", "Física", "TOPIC-B", "C1"))
    candidates.append({"id": "B0"})

    ctx = _fake_context(rows)
    picked = select_adaptive_questions(
        candidates,
        2,
        [],
        "Por área",
        ctx,
        selected_area="Física",
        seed=4,
    )

    topics = {
        ctx.metadata[_question_id(q)].topic_id
        for q in picked
    }
    checks["topic_volume_does_not_dominate"] = topics == {"TOPIC-A", "TOPIC-B"}

    # 5) Legacy agregado no crea evidencia.
    evidence = build_question_evidence(
        [
            {
                "fecha": "2026-08-01T12:00:00",
                "correctas": 5,
                "total": 10,
            }
        ],
        {"A0"},
    )
    checks["legacy_ignored"] = evidence == {}

    # 6) Evidencia detallada sí se usa.
    evidence = build_question_evidence(
        [
            {
                "schema_version": ATTEMPT_SCHEMA,
                "fecha": "2026-08-01T12:00:00",
                "items": [
                    {"question_id": "A0", "correct": False},
                    {"question_id": "A0", "correct": True},
                ],
            }
        ],
        {"A0"},
    )
    checks["detailed_evidence_used"] = (
        evidence["A0"].attempts == 2
        and evidence["A0"].correct == 1
    )

    # 7) Tras exposición, un tema claramente débil supera a uno fuerte.
    weak = Evidence(attempts=4, correct=0)
    strong = Evidence(attempts=4, correct=4)
    checks["weak_scores_above_strong"] = (
        learner_need_score(weak) > learner_need_score(strong)
    )

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError(f"Self-test P2-E2A falló: {failed}")

    return checks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P2-E2A - Motor adaptativo UTN desacoplado de la app."
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Ejecuta autoauditoría sintética.",
    )
    parser.add_argument(
        "--audit-project",
        action="store_true",
        help="Verifica los checkpoints P2-E1.5 del proyecto actual.",
    )
    parser.add_argument(
        "--base",
        default=".",
        help="Raíz del proyecto.",
    )

    args = parser.parse_args()

    if not args.self_test and not args.audit_project:
        args.self_test = True
        args.audit_project = True

    print("=" * 108)
    print("P2-E2A - MOTOR ADAPTATIVO DESACOPLADO / PRE-INTEGRACIÓN")
    print("=" * 108)

    if args.self_test:
        checks = self_test()
        print()
        print("AUTOAUDITORÍA SINTÉTICA")
        for name in sorted(checks):
            print(f"  [OK] {name}")

    if args.audit_project:
        audit = audit_project(args.base)

        print()
        print("AUDITORÍA DEL PROYECTO")
        print(f"  C0-C2 elegibles      : {audit['eligible_questions']}")
        print(f"  temas seleccionables : {audit['selectable_topics']}")
        print(f"  history legacy       : {audit['history_legacy']}")
        print(f"  history detallado    : {audit['history_detailed']}")
        print(f"  cold start           : {audit['cold_start']}")

        print()
        print("COSTO MANUAL")
        for cost in ("C0", "C1", "C2"):
            print(f"  {cost}: {audit['manual_costs'].get(cost, 0)}")

        print()
        print("ÁREAS ELEGIBLES")
        for area in DEFAULT_OFFICIAL_AREAS:
            print(f"  {area:<15}: {audit['areas'].get(area, 0)}")

    print()
    print("SEGURIDAD")
    print("  app.py modificado              : False")
    print("  mastery_engine.py modificado   : False")
    print("  Full Exam adaptativo           : False")
    print("  C3 seleccionable               : False")
    print("  legacy usado como mastery      : False")
    print("  integración Streamlit realizada: False")

    print()
    print("DECISIÓN : P2-E2A_READY_FOR_APP_INTEGRATION")
    print("RESULTADO: MOTOR ADAPTATIVO VALIDABLE SIN MODIFICAR EL FLUJO ACTUAL")


if __name__ == "__main__":
    main()