from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Protocol
import json
import time
import uuid

ANALYTICS_SCHEMA_VERSION = "p2_analytics_v1"
DEFAULT_APP_VERSION = "P2-MOBILE-M5A.3"
DEFAULT_BATCH_SIZE = 5
DEFAULT_IDLE_AFTER_SECONDS = 120
DEFAULT_STALE_AFTER_SECONDS = 600
DEFAULT_MAX_RESPONSE_SECONDS = 900

EVENT_CATEGORY = {
    "SESSION_STARTED": "session",
    "SESSION_HEARTBEAT": "session",
    "SESSION_IDLE": "session",
    "SESSION_REACTIVATED": "session",
    "SESSION_ENDED": "session",
    "SESSION_EXPIRED": "session",
    "PAGE_VIEWED": "navigation",
    "PRACTICE_STARTED": "attempt",
    "PRACTICE_COMPLETED": "attempt",
    "PRACTICE_ABANDONED": "attempt",
    "EXAM_STARTED": "attempt",
    "EXAM_COMPLETED": "attempt",
    "EXAM_ABANDONED": "attempt",
    "QUESTION_VIEWED": "question",
    "ANSWER_SELECTED": "question",
    "ANSWER_CHANGED": "question",
    "QUESTION_FLAGGED": "question",
    "QUESTION_UNFLAGGED": "question",
    "EXPLANATION_VIEWED": "learning",
    "RESULTS_VIEWED": "learning",
    "RECOMMENDATIONS_VIEWED": "learning",
    "LOGIN_SUCCESS": "auth",
    "LOGIN_FAILED": "auth",
    "LOGOUT": "auth",
    "SYNC_SUCCESS": "system",
    "SYNC_FAILED": "system",
    "ANALYTICS_ERROR": "system",
}

QUESTION_EVENTS = {
    "QUESTION_VIEWED",
    "ANSWER_SELECTED",
    "ANSWER_CHANGED",
    "QUESTION_FLAGGED",
    "QUESTION_UNFLAGGED",
    "EXPLANATION_VIEWED",
}
ATTEMPT_EVENTS = {
    "PRACTICE_STARTED",
    "PRACTICE_COMPLETED",
    "PRACTICE_ABANDONED",
    "EXAM_STARTED",
    "EXAM_COMPLETED",
    "EXAM_ABANDONED",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def canonical_answer_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 1 and text.isalpha():
        return text.upper()
    if len(text) >= 2 and text[0].isalpha() and (
        text[1].isspace() or text[1] in ").:-"
    ):
        return text[0].upper()
    return text.upper()


def _hash_key(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return sha256(raw.encode("utf-8")).hexdigest()


class AnalyticsStore(Protocol):
    def create_session(self, row: dict[str, Any]) -> None: ...
    def update_session(self, session_id: str, values: dict[str, Any]) -> None: ...
    def ingest_events(self, rows: list[dict[str, Any]]) -> dict[str, Any]: ...
    def create_attempt(self, row: dict[str, Any]) -> None: ...
    def update_attempt(self, attempt_id: str, values: dict[str, Any]) -> None: ...
    def upsert_attempt_item(self, row: dict[str, Any]) -> None: ...
    def reconcile_stale_sessions(self) -> Any: ...


class SupabaseAnalyticsStore:
    """Persistencia cloud. Debe usar la Secret Key del servidor."""

    def __init__(self, url: str, secret_key: str):
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError("Falta instalar el paquete 'supabase'.") from exc
        self.client = create_client(str(url).strip(), str(secret_key).strip())

    def create_session(self, row: dict[str, Any]) -> None:
        self.client.table("p2_analytics_sessions").insert(row).execute()

    def update_session(self, session_id: str, values: dict[str, Any]) -> None:
        if not values:
            return
        self.client.table("p2_analytics_sessions").update(values).eq(
            "session_id", session_id
        ).execute()

    def ingest_events(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"received": 0, "accepted": 0, "duplicates": 0}
        response = self.client.rpc(
            "p2_analytics_ingest_events", {"p_events": rows}
        ).execute()
        data = getattr(response, "data", None)
        return data if isinstance(data, dict) else {"received": len(rows)}

    def create_attempt(self, row: dict[str, Any]) -> None:
        self.client.table("p2_analytics_attempts").insert(row).execute()

    def update_attempt(self, attempt_id: str, values: dict[str, Any]) -> None:
        if not values:
            return
        self.client.table("p2_analytics_attempts").update(values).eq(
            "attempt_id", attempt_id
        ).execute()

    def upsert_attempt_item(self, row: dict[str, Any]) -> None:
        self.client.table("p2_analytics_attempt_items").upsert(
            row, on_conflict="attempt_id,question_id"
        ).execute()

    def reconcile_stale_sessions(self) -> Any:
        response = self.client.rpc("p2_analytics_reconcile_stale_sessions").execute()
        return getattr(response, "data", None)


class NoOpAnalyticsStore:
    """Fallback seguro: nunca bloquea el simulador."""

    def create_session(self, row: dict[str, Any]) -> None:
        return None

    def update_session(self, session_id: str, values: dict[str, Any]) -> None:
        return None

    def ingest_events(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"received": len(rows), "accepted": 0, "duplicates": 0, "noop": True}

    def create_attempt(self, row: dict[str, Any]) -> None:
        return None

    def update_attempt(self, attempt_id: str, values: dict[str, Any]) -> None:
        return None

    def upsert_attempt_item(self, row: dict[str, Any]) -> None:
        return None

    def reconcile_stale_sessions(self) -> Any:
        return None


@dataclass
class AnalyticsHealth:
    events_enqueued: int = 0
    events_flushed: int = 0
    flushes: int = 0
    duplicates_prevented: int = 0
    store_errors: int = 0
    last_error: str | None = None
    last_flush_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "events_enqueued": self.events_enqueued,
            "events_flushed": self.events_flushed,
            "flushes": self.flushes,
            "duplicates_prevented": self.duplicates_prevented,
            "store_errors": self.store_errors,
            "last_error": self.last_error,
            "last_flush_at": self.last_flush_at,
            "healthy": self.store_errors == 0,
        }


@dataclass
class QuestionRuntime:
    first_view_at: datetime
    last_view_at: datetime
    view_count: int = 1
    first_answer: str | None = None
    final_answer: str | None = None
    answer_change_count: int = 0
    flagged: bool = False
    explanation_viewed: bool = False


@dataclass
class AnalyticsEngine:
    store: AnalyticsStore
    user_id: str
    app_version: str = DEFAULT_APP_VERSION
    analytics_schema_version: str = ANALYTICS_SCHEMA_VERSION
    batch_size: int = DEFAULT_BATCH_SIZE
    idle_after_seconds: int = DEFAULT_IDLE_AFTER_SECONDS
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
    max_response_seconds: int = DEFAULT_MAX_RESPONSE_SECONDS
    fail_silent: bool = True
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        self.user_id = str(self.user_id)
        self._sequence = 0
        self._queue: list[dict[str, Any]] = []
        self._queued_keys: set[str] = set()
        self._session_started_at: datetime | None = None
        self._last_heartbeat_at: datetime | None = None
        self._last_interaction_at: datetime | None = None
        self._session_status = "active"
        self._page: str | None = None
        self._mode: str | None = None
        self._attempt_id: str | None = None
        self._attempt_type: str | None = None
        self._attempt_started_at: datetime | None = None
        self._current_question_id: str | None = None
        self._current_question_order: int | None = None
        self._question_runtime: dict[str, QuestionRuntime] = {}
        self._page_open_seconds = 0
        self._interaction_active_seconds = 0
        self._idle_seconds = 0
        self.health = AnalyticsHealth()

    @property
    def attempt_id(self) -> str | None:
        return self._attempt_id

    def _guard(self, action, fallback=None):
        try:
            return action()
        except Exception as exc:
            self.health.store_errors += 1
            self.health.last_error = f"{type(exc).__name__}: {exc}"
            if self.fail_silent:
                return fallback
            raise

    def _interaction(self, now: datetime | None = None) -> None:
        now = now or utcnow()
        was_idle = self._session_status == "idle"
        self._last_interaction_at = now
        self._session_status = "active"
        if was_idle:
            self.track(
                "SESSION_REACTIVATED",
                metadata={"reason": "interaction_after_idle"},
                critical=True,
                semantic_scope=f"reactivated:{int(now.timestamp()) // 60}",
                occurred_at=now,
            )

    def start_session(self, client_instance_id: str | None = None) -> str:
        if self._session_started_at is not None:
            return self.session_id
        now = utcnow()
        self._session_started_at = now
        self._last_heartbeat_at = now
        self._last_interaction_at = now
        row = {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "client_instance_id": _text(client_instance_id),
            "started_at": iso(now),
            "last_seen_at": iso(now),
            "last_interaction_at": iso(now),
            "status": "active",
            "app_version": self.app_version,
            "analytics_schema_version": self.analytics_schema_version,
        }
        self._guard(lambda: self.store.create_session(row))
        self.track(
            "SESSION_STARTED",
            metadata={"client_instance_id": _text(client_instance_id)},
            critical=True,
            semantic_scope="start",
            occurred_at=now,
        )
        return self.session_id

    def track(
        self,
        event_name: str,
        *,
        page: str | None = None,
        mode: str | None = None,
        question: dict[str, Any] | None = None,
        attempt_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        critical: bool = False,
        semantic_scope: str | None = None,
        occurred_at: datetime | None = None,
    ) -> bool:
        if event_name not in EVENT_CATEGORY:
            raise ValueError(f"Evento no permitido: {event_name}")
        if self._session_started_at is None and event_name != "SESSION_STARTED":
            self.start_session()

        now = occurred_at or utcnow()
        question_id = _text((question or {}).get("id"))
        effective_attempt = attempt_id or self._attempt_id

        if event_name in QUESTION_EVENTS and not question_id:
            raise ValueError(f"{event_name} requiere question_id")
        if event_name in ATTEMPT_EVENTS and not effective_attempt:
            raise ValueError(f"{event_name} requiere attempt_id")

        scope = semantic_scope or str(uuid.uuid4())
        idem = _hash_key(self.session_id, event_name, scope)
        if idem in self._queued_keys:
            self.health.duplicates_prevented += 1
            return False

        self._sequence += 1
        row = {
            "event_id": str(uuid.uuid4()),
            "idempotency_key": idem,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "attempt_id": effective_attempt,
            "sequence_no": self._sequence,
            "event_name": event_name,
            "event_category": EVENT_CATEGORY[event_name],
            "page": _text(page or self._page),
            "mode": _text(mode or self._mode),
            "question_id": question_id,
            "area": _text((question or {}).get("subject") or (question or {}).get("area")),
            "topic": _text((question or {}).get("topic")),
            "occurred_at": iso(now),
            "app_version": self.app_version,
            "analytics_schema_version": self.analytics_schema_version,
            "metadata_json": metadata or {},
        }
        self._queue.append(row)
        self._queued_keys.add(idem)
        self.health.events_enqueued += 1

        if event_name != "SESSION_HEARTBEAT":
            self._last_interaction_at = now

        if critical or len(self._queue) >= self.batch_size:
            self.flush()
        return True

    def flush(self) -> bool:
        if not self._queue:
            return True
        batch = list(self._queue)

        def action():
            return self.store.ingest_events(batch)

        result = self._guard(action, fallback=None)
        if result is None and self.fail_silent and self.health.last_error:
            return False

        for event in batch:
            self._queued_keys.discard(event["idempotency_key"])
        self._queue = self._queue[len(batch):]
        self.health.events_flushed += len(batch)
        self.health.flushes += 1
        self.health.last_flush_at = iso()
        return True

    def page_view(self, page: str, mode: str | None = None) -> bool:
        page = str(page)
        if self._page == page and (mode is None or self._mode == mode):
            self.health.duplicates_prevented += 1
            return False
        self._interaction()
        self._page = page
        if mode is not None:
            self._mode = str(mode)
        return self.track(
            "PAGE_VIEWED",
            page=self._page,
            mode=self._mode,
            metadata={},
            semantic_scope=f"page:{self._sequence + 1}:{self._page}:{self._mode}",
        )

    def heartbeat(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        if self._session_started_at is None:
            self.start_session()
        assert self._session_started_at is not None
        previous = self._last_heartbeat_at or self._session_started_at
        delta = max(0, int((now - previous).total_seconds()))
        self._page_open_seconds = max(
            self._page_open_seconds,
            int((now - self._session_started_at).total_seconds()),
        )
        inactivity = int((now - (self._last_interaction_at or now)).total_seconds())
        if inactivity >= self.idle_after_seconds:
            self._idle_seconds += delta
            if self._session_status != "idle":
                self._session_status = "idle"
                self.track(
                    "SESSION_IDLE",
                    metadata={"inactive_seconds": inactivity},
                    critical=True,
                    semantic_scope=f"idle:{int(now.timestamp()) // 60}",
                    occurred_at=now,
                )
        else:
            self._interaction_active_seconds += delta
        self._last_heartbeat_at = now

        values = {
            "last_seen_at": iso(now),
            "last_heartbeat_at": iso(now),
            "status": self._session_status,
            "page_open_seconds": self._page_open_seconds,
            "interaction_active_seconds": self._interaction_active_seconds,
            "idle_seconds": self._idle_seconds,
            "current_page": self._page,
            "current_mode": self._mode,
            "current_attempt_id": self._attempt_id,
            "current_question_id": self._current_question_id,
            "current_question_order": self._current_question_order,
            "updated_at": iso(now),
        }
        self._guard(lambda: self.store.update_session(self.session_id, values))
        bucket = int(now.timestamp()) // 60
        return self.track(
            "SESSION_HEARTBEAT",
            page=self._page,
            mode=self._mode,
            metadata={
                "page_open_seconds": self._page_open_seconds,
                "interaction_active_seconds": self._interaction_active_seconds,
                "idle_seconds": self._idle_seconds,
                "status": self._session_status,
            },
            critical=True,
            semantic_scope=f"heartbeat:{bucket}",
            occurred_at=now,
        )

    def start_attempt(
        self,
        *,
        attempt_type: str,
        mode: str,
        title: str | None,
        question_count: int,
        selection_strategy: str | None = None,
    ) -> str:
        if attempt_type not in {"practice", "exam"}:
            raise ValueError("attempt_type debe ser practice o exam")
        self._interaction()
        now = utcnow()
        attempt_id = str(uuid.uuid4())
        self._attempt_id = attempt_id
        self._attempt_type = attempt_type
        self._attempt_started_at = now
        self._mode = str(mode)
        self._current_question_id = None
        self._current_question_order = None
        self._question_runtime = {}
        row = {
            "attempt_id": attempt_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "attempt_type": attempt_type,
            "mode": str(mode),
            "title": _text(title),
            "selection_strategy": _text(selection_strategy),
            "started_at": iso(now),
            "last_activity_at": iso(now),
            "status": "in_progress",
            "question_count": max(0, int(question_count)),
            "app_version": self.app_version,
            "analytics_schema_version": self.analytics_schema_version,
        }
        self._guard(lambda: self.store.create_attempt(row))
        event = "PRACTICE_STARTED" if attempt_type == "practice" else "EXAM_STARTED"
        self.track(
            event,
            mode=self._mode,
            attempt_id=attempt_id,
            metadata={
                "title": _text(title),
                "question_count": max(0, int(question_count)),
                "selection_strategy": _text(selection_strategy),
            },
            critical=True,
            semantic_scope=f"attempt:{attempt_id}:start",
            occurred_at=now,
        )
        self._guard(
            lambda: self.store.update_session(
                self.session_id,
                {
                    "current_attempt_id": attempt_id,
                    "current_mode": self._mode,
                    "updated_at": iso(now),
                },
            )
        )
        return attempt_id

    def question_view(self, question: dict[str, Any], order: int) -> bool:
        if not self._attempt_id:
            return False
        question_id = str(question.get("id") or "").strip()
        if not question_id:
            return False
        order = max(1, int(order))
        now = utcnow()
        self._interaction(now)

        runtime = self._question_runtime.get(question_id)
        is_same_current = self._current_question_id == question_id
        if runtime is None:
            runtime = QuestionRuntime(first_view_at=now, last_view_at=now)
            self._question_runtime[question_id] = runtime
        elif not is_same_current:
            runtime.last_view_at = now
            runtime.view_count += 1

        self._current_question_id = question_id
        self._current_question_order = order
        self._guard(
            lambda: self.store.upsert_attempt_item(
                {
                    "attempt_id": self._attempt_id,
                    "question_id": question_id,
                    "question_order": order,
                    "area": _text(question.get("subject") or question.get("area")),
                    "topic": _text(question.get("topic")),
                    "skill": _text(question.get("skill")),
                    "subskill": _text(question.get("subskill")),
                    "first_view_at": iso(runtime.first_view_at),
                    "last_view_at": iso(runtime.last_view_at),
                    "answer_change_count": runtime.answer_change_count,
                    "flagged": runtime.flagged,
                    "explanation_viewed": runtime.explanation_viewed,
                    "view_count": runtime.view_count,
                    "updated_at": iso(now),
                }
            )
        )
        self._guard(
            lambda: self.store.update_attempt(
                self._attempt_id,
                {"last_activity_at": iso(now), "updated_at": iso(now)},
            )
        )
        if is_same_current:
            self.health.duplicates_prevented += 1
            return False
        return self.track(
            "QUESTION_VIEWED",
            question=question,
            metadata={"question_order": order, "view_count": runtime.view_count},
            semantic_scope=f"question:{question_id}:view:{runtime.view_count}",
            occurred_at=now,
        )

    def answer_selected(self, question: dict[str, Any], answer: Any) -> bool:
        if not self._attempt_id:
            return False
        question_id = str(question.get("id") or "").strip()
        if not question_id:
            return False
        now = utcnow()
        self._interaction(now)
        if question_id not in self._question_runtime:
            self.question_view(question, self._current_question_order or 1)
        runtime = self._question_runtime[question_id]
        answer_key = canonical_answer_key(answer)
        if not answer_key:
            return False
        previous = runtime.final_answer
        if previous == answer_key:
            self.health.duplicates_prevented += 1
            return False
        event_name = "ANSWER_SELECTED" if runtime.first_answer is None else "ANSWER_CHANGED"
        if runtime.first_answer is None:
            runtime.first_answer = answer_key
        else:
            runtime.answer_change_count += 1
        runtime.final_answer = answer_key
        response_seconds = min(
            self.max_response_seconds,
            max(0, int((now - runtime.first_view_at).total_seconds())),
        )
        correct_answer = canonical_answer_key(question.get("answer")) or None
        self._guard(
            lambda: self.store.upsert_attempt_item(
                {
                    "attempt_id": self._attempt_id,
                    "question_id": question_id,
                    "question_order": self._current_question_order or 1,
                    "area": _text(question.get("subject") or question.get("area")),
                    "topic": _text(question.get("topic")),
                    "skill": _text(question.get("skill")),
                    "subskill": _text(question.get("subskill")),
                    "first_view_at": iso(runtime.first_view_at),
                    "last_view_at": iso(runtime.last_view_at),
                    "answered_at": iso(now),
                    "first_answer": runtime.first_answer,
                    "final_answer": runtime.final_answer,
                    "correct_answer": correct_answer,
                    "correct": None,
                    "response_seconds": response_seconds,
                    "answer_change_count": runtime.answer_change_count,
                    "flagged": runtime.flagged,
                    "explanation_viewed": runtime.explanation_viewed,
                    "view_count": runtime.view_count,
                    "updated_at": iso(now),
                }
            )
        )
        return self.track(
            event_name,
            question=question,
            metadata={
                "answer_key": answer_key,
                "previous_answer_key": previous,
                "answer_change_count": runtime.answer_change_count,
                "response_seconds": response_seconds,
            },
            semantic_scope=f"question:{question_id}:answer:{runtime.answer_change_count}:{answer_key}",
            occurred_at=now,
        )

    def set_flag(self, question: dict[str, Any], flagged: bool) -> bool:
        if not self._attempt_id:
            return False
        question_id = str(question.get("id") or "").strip()
        runtime = self._question_runtime.get(question_id)
        if runtime is None:
            return False
        flagged = bool(flagged)
        if runtime.flagged == flagged:
            self.health.duplicates_prevented += 1
            return False
        now = utcnow()
        self._interaction(now)
        runtime.flagged = flagged
        self._guard(
            lambda: self.store.upsert_attempt_item(
                {
                    "attempt_id": self._attempt_id,
                    "question_id": question_id,
                    "question_order": self._current_question_order or 1,
                    "flagged": flagged,
                    "answer_change_count": runtime.answer_change_count,
                    "explanation_viewed": runtime.explanation_viewed,
                    "view_count": runtime.view_count,
                    "updated_at": iso(now),
                }
            )
        )
        event = "QUESTION_FLAGGED" if flagged else "QUESTION_UNFLAGGED"
        return self.track(
            event,
            question=question,
            metadata={"flagged": flagged},
            semantic_scope=f"question:{question_id}:flag:{int(flagged)}:{self._sequence + 1}",
            occurred_at=now,
        )

    def explanation_viewed(self, question: dict[str, Any]) -> bool:
        if not self._attempt_id:
            return False
        question_id = str(question.get("id") or "").strip()
        runtime = self._question_runtime.get(question_id)
        if runtime is None:
            return False
        if runtime.explanation_viewed:
            self.health.duplicates_prevented += 1
            return False
        now = utcnow()
        self._interaction(now)
        runtime.explanation_viewed = True
        self._guard(
            lambda: self.store.upsert_attempt_item(
                {
                    "attempt_id": self._attempt_id,
                    "question_id": question_id,
                    "question_order": self._current_question_order or 1,
                    "explanation_viewed": True,
                    "answer_change_count": runtime.answer_change_count,
                    "flagged": runtime.flagged,
                    "view_count": runtime.view_count,
                    "updated_at": iso(now),
                }
            )
        )
        return self.track(
            "EXPLANATION_VIEWED",
            question=question,
            metadata={},
            semantic_scope=f"question:{question_id}:explanation",
            occurred_at=now,
        )

    def complete_attempt(
        self,
        *,
        correct_count: int,
        total: int,
        percentage: float,
        detailed_items: list[dict[str, Any]] | None = None,
    ) -> bool:
        if not self._attempt_id:
            return False
        now = utcnow()
        attempt_id = self._attempt_id
        elapsed = 0
        if self._attempt_started_at is not None:
            elapsed = max(0, int((now - self._attempt_started_at).total_seconds()))
        items = detailed_items or []
        for item in items:
            qid = str(item.get("question_id") or "").strip()
            if not qid:
                continue
            runtime = self._question_runtime.get(qid)
            self._guard(
                lambda item=item, qid=qid, runtime=runtime: self.store.upsert_attempt_item(
                    {
                        "attempt_id": attempt_id,
                        "question_id": qid,
                        "question_order": (
                            self._current_question_order or 1
                            if runtime is not None and qid == self._current_question_id
                            else int(item.get("question_order") or 1)
                        ),
                        "area": _text(item.get("area")),
                        "topic": _text(item.get("topic")),
                        "skill": _text(item.get("skill")),
                        "subskill": _text(item.get("subskill")),
                        "first_answer": canonical_answer_key(item.get("user_answer")) or None,
                        "final_answer": canonical_answer_key(item.get("user_answer")) or None,
                        "correct_answer": canonical_answer_key(item.get("correct_answer")) or None,
                        "correct": bool(item.get("correct")) if item.get("answered") else None,
                        "answer_change_count": runtime.answer_change_count if runtime else 0,
                        "flagged": runtime.flagged if runtime else False,
                        "explanation_viewed": runtime.explanation_viewed if runtime else False,
                        "view_count": runtime.view_count if runtime else 0,
                        "updated_at": iso(now),
                    }
                )
            )
        answered = sum(1 for item in items if item.get("answered")) if items else 0
        viewed = len({qid for qid in self._question_runtime})
        self._guard(
            lambda: self.store.update_attempt(
                attempt_id,
                {
                    "last_activity_at": iso(now),
                    "finished_at": iso(now),
                    "status": "completed",
                    "questions_viewed": viewed,
                    "questions_answered": answered,
                    "correct_count": max(0, int(correct_count)),
                    "percentage": max(0.0, min(100.0, float(percentage))),
                    "elapsed_seconds": elapsed,
                    "updated_at": iso(now),
                },
            )
        )
        event = "PRACTICE_COMPLETED" if self._attempt_type == "practice" else "EXAM_COMPLETED"
        tracked = self.track(
            event,
            attempt_id=attempt_id,
            metadata={
                "correct_count": int(correct_count),
                "total": int(total),
                "percentage": float(percentage),
                "questions_viewed": viewed,
                "questions_answered": answered,
                "elapsed_seconds": elapsed,
            },
            critical=True,
            semantic_scope=f"attempt:{attempt_id}:completed",
            occurred_at=now,
        )
        self._attempt_id = None
        self._attempt_type = None
        self._attempt_started_at = None
        self._current_question_id = None
        self._current_question_order = None
        self._guard(
            lambda: self.store.update_session(
                self.session_id,
                {
                    "current_attempt_id": None,
                    "current_question_id": None,
                    "current_question_order": None,
                    "updated_at": iso(now),
                },
            )
        )
        return tracked

    def abandon_attempt(self, reason: str = "user_exit") -> bool:
        if not self._attempt_id:
            return False
        now = utcnow()
        attempt_id = self._attempt_id
        elapsed = 0
        if self._attempt_started_at is not None:
            elapsed = max(0, int((now - self._attempt_started_at).total_seconds()))
        self._guard(
            lambda: self.store.update_attempt(
                attempt_id,
                {
                    "last_activity_at": iso(now),
                    "abandoned_at": iso(now),
                    "status": "abandoned",
                    "questions_viewed": len(self._question_runtime),
                    "questions_answered": sum(
                        1 for q in self._question_runtime.values() if q.final_answer
                    ),
                    "elapsed_seconds": elapsed,
                    "updated_at": iso(now),
                },
            )
        )
        event = "PRACTICE_ABANDONED" if self._attempt_type == "practice" else "EXAM_ABANDONED"
        tracked = self.track(
            event,
            attempt_id=attempt_id,
            metadata={"reason": reason, "elapsed_seconds": elapsed},
            critical=True,
            semantic_scope=f"attempt:{attempt_id}:abandoned",
            occurred_at=now,
        )
        self._attempt_id = None
        self._attempt_type = None
        self._attempt_started_at = None
        self._current_question_id = None
        self._current_question_order = None
        return tracked

    def results_viewed(self) -> bool:
        self._interaction()
        return self.track(
            "RESULTS_VIEWED",
            metadata={},
            semantic_scope=f"results:{self._sequence + 1}",
        )

    def recommendations_viewed(self) -> bool:
        self._interaction()
        return self.track(
            "RECOMMENDATIONS_VIEWED",
            metadata={},
            semantic_scope=f"recommendations:{self._sequence + 1}",
        )

    def end_session(self, reason: str = "logout") -> bool:
        if self._session_started_at is None:
            return False
        now = utcnow()
        if self._attempt_id:
            self.abandon_attempt(reason="session_end")
        self._interaction(now)
        event_ok = self.track(
            "LOGOUT" if reason == "logout" else "SESSION_ENDED",
            metadata={"reason": reason},
            critical=True,
            semantic_scope=f"end:{reason}",
            occurred_at=now,
        )
        self.flush()
        self._session_status = "ended"
        self._guard(
            lambda: self.store.update_session(
                self.session_id,
                {
                    "last_seen_at": iso(now),
                    "last_interaction_at": iso(now),
                    "ended_at": iso(now),
                    "status": "ended",
                    "ended_reason": reason,
                    "page_open_seconds": self._page_open_seconds,
                    "interaction_active_seconds": self._interaction_active_seconds,
                    "idle_seconds": self._idle_seconds,
                    "current_attempt_id": None,
                    "current_question_id": None,
                    "current_question_order": None,
                    "updated_at": iso(now),
                },
            )
        )
        return event_ok

    def reconcile_stale(self) -> Any:
        return self._guard(lambda: self.store.reconcile_stale_sessions())

    def health_snapshot(self) -> dict[str, Any]:
        out = self.health.as_dict()
        out.update(
            {
                "session_id": self.session_id,
                "session_status": self._session_status,
                "queue_size": len(self._queue),
                "current_page": self._page,
                "current_mode": self._mode,
                "current_attempt_id": self._attempt_id,
                "current_question_id": self._current_question_id,
                "page_open_seconds": self._page_open_seconds,
                "interaction_active_seconds": self._interaction_active_seconds,
                "idle_seconds": self._idle_seconds,
            }
        )
        return out


def build_store_from_streamlit(st, base=None) -> AnalyticsStore:
    """Construye Store sin duplicar Secrets del proyecto Mobile."""
    try:
        local_requested = str(__import__("os").environ.get("P2_MOBILE_LOCAL", "")).strip() == "1"
        mobile_cfg = st.secrets.get("mobile", {}) if hasattr(st, "secrets") else {}
        if str(mobile_cfg.get("backend", "")).strip().lower() == "local":
            local_requested = True
        if local_requested:
            return NoOpAnalyticsStore()
        cfg = st.secrets.get("supabase", {})
        url = str(cfg.get("url", "")).strip()
        key = str(cfg.get("secret_key", cfg.get("service_role_key", ""))).strip()
        if not url or not key:
            return NoOpAnalyticsStore()
        return SupabaseAnalyticsStore(url, key)
    except Exception:
        return NoOpAnalyticsStore()


def build_engine_for_mobile(
    st,
    mobile_ctx: dict[str, Any],
    *,
    app_version: str = DEFAULT_APP_VERSION,
) -> AnalyticsEngine:
    """Factory preparada para M5A.4. No inicia eventos por sí sola."""
    store = build_store_from_streamlit(st)
    return AnalyticsEngine(
        store=store,
        user_id=str(mobile_ctx["user_id"]),
        app_version=app_version,
        fail_silent=True,
    )
