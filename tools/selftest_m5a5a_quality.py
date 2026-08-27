from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics_engine import AnalyticsEngine, utcnow


class MemoryStore:
    def __init__(self):
        self.sessions = {}
        self.events = []
        self.seen = set()
        self.attempts = {}
        self.items = {}

    def create_session(self, row):
        self.sessions[row["session_id"]] = dict(row)

    def update_session(self, session_id, values):
        self.sessions.setdefault(session_id, {}).update(values)

    def ingest_events(self, rows):
        accepted = 0
        duplicates = 0
        for row in rows:
            key = row["idempotency_key"]
            if key in self.seen:
                duplicates += 1
                continue
            self.seen.add(key)
            self.events.append(dict(row))
            accepted += 1
        return {"received": len(rows), "accepted": accepted, "duplicates": duplicates}

    def create_attempt(self, row):
        self.attempts[row["attempt_id"]] = dict(row)

    def update_attempt(self, attempt_id, values):
        self.attempts.setdefault(attempt_id, {}).update(values)

    def upsert_attempt_item(self, row):
        key = (row["attempt_id"], row["question_id"])
        current = self.items.get(key, {})
        current.update(row)
        self.items[key] = current

    def reconcile_stale_sessions(self):
        return {"ok": True}


def main():
    store = MemoryStore()
    engine = AnalyticsEngine(store=store, user_id=str(uuid.uuid4()), app_version="P2-MOBILE-M5A.5A")
    engine.start_session(client_instance_id="quality-test")
    engine.page_view("Simulador")
    attempt_id = engine.start_attempt(
        attempt_type="practice",
        mode="Práctica",
        title="M5A.5A quality test",
        question_count=2,
        selection_strategy="standard_v1",
    )

    q1 = {"id": "Q-1", "subject": "Física", "topic": "T1", "answer": "B"}
    q2 = {"id": "Q-2", "subject": "Matemática", "topic": "T2", "answer": "C"}

    assert engine.question_view(q1, 1)
    assert engine.answer_selected(q1, "A")
    assert engine.answer_selected(q1, "B")
    assert engine.set_flag(q1, True)

    assert engine.question_view(q2, 2)
    assert engine.answer_selected(q2, "C")

    base = engine._last_interaction_at or utcnow()
    hb = base.replace(second=10, microsecond=0) + timedelta(minutes=1)
    engine.heartbeat(hb)
    seq_after_first_hb = engine._sequence
    engine.heartbeat(hb + timedelta(seconds=1))
    assert engine._sequence == seq_after_first_hb, "same heartbeat bucket consumed sequence"

    assert engine.results_viewed()
    details = [
        {"question_id": "Q-1", "answered": True, "correct": True, "user_answer": "B", "correct_answer": "B", "area": "Física"},
        {"question_id": "Q-2", "answered": True, "correct": True, "user_answer": "C", "correct_answer": "C", "area": "Matemática"},
    ]
    assert engine.complete_attempt(correct_count=2, total=2, percentage=100.0, detailed_items=details)
    assert engine.end_session("logout")
    engine.flush()

    names = [e["event_name"] for e in store.events]
    assert "ANSWER_CHANGED" in names
    assert "QUESTION_FLAGGED" in names
    assert "RESULTS_VIEWED" in names
    assert "LOGOUT" in names
    assert "SESSION_ENDED" in names

    q1_item = store.items[(attempt_id, "Q-1")]
    q2_item = store.items[(attempt_id, "Q-2")]
    assert q1_item["question_order"] == 1, q1_item
    assert q2_item["question_order"] == 2, q2_item
    assert q1_item["answer_change_count"] == 1
    assert q1_item["flagged"] is True

    result_events = [e for e in store.events if e["event_name"] == "RESULTS_VIEWED"]
    assert result_events and result_events[-1]["page"] == "Resultados"

    seqs = [int(e["sequence_no"]) for e in store.events]
    assert seqs == list(range(1, len(seqs) + 1)), seqs

    session = store.sessions[engine.session_id]
    assert session["status"] == "ended"
    assert session["ended_reason"] == "logout"

    print("P2-M5A.5A QUALITY SELF-TEST")
    print("=" * 58)
    print("[PASS] question_order preserved")
    print("[PASS] ANSWER_CHANGED tracked")
    print("[PASS] QUESTION_FLAGGED tracked")
    print("[PASS] same-bucket duplicate does not consume sequence")
    print("[PASS] RESULTS page state")
    print("[PASS] LOGOUT + SESSION_ENDED")
    print("DECISION : P2-M5A.5A QUALITY PASS")


if __name__ == "__main__":
    main()
