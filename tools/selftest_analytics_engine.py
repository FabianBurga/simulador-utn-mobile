from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analytics_engine import AnalyticsEngine, utcnow


class MemoryStore:
    def __init__(self):
        self.sessions = {}
        self.events = []
        self.attempts = {}
        self.items = {}
        self.reconciled = 0

    def create_session(self, row):
        self.sessions[row['session_id']] = dict(row)

    def update_session(self, session_id, values):
        self.sessions.setdefault(session_id, {}).update(dict(values))

    def ingest_events(self, rows):
        existing = {e['idempotency_key'] for e in self.events}
        accepted = 0
        duplicates = 0
        for row in rows:
            if row['idempotency_key'] in existing:
                duplicates += 1
                continue
            self.events.append(dict(row))
            existing.add(row['idempotency_key'])
            accepted += 1
        return {'received': len(rows), 'accepted': accepted, 'duplicates': duplicates}

    def create_attempt(self, row):
        self.attempts[row['attempt_id']] = dict(row)

    def update_attempt(self, attempt_id, values):
        self.attempts.setdefault(attempt_id, {}).update(dict(values))

    def upsert_attempt_item(self, row):
        key = (row['attempt_id'], row['question_id'])
        self.items.setdefault(key, {}).update(dict(row))

    def reconcile_stale_sessions(self):
        self.reconciled += 1
        return {'ok': True}


class FailingStore(MemoryStore):
    def ingest_events(self, rows):
        raise RuntimeError('simulated outage')


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


def main():
    store = MemoryStore()
    eng = AnalyticsEngine(store=store, user_id='00000000-0000-0000-0000-000000000001')
    sid = eng.start_session(client_instance_id='SELFTEST')
    assert_true(sid in store.sessions, 'session not created')

    # Page rerun dedupe.
    assert_true(eng.page_view('Simulador') is True, 'first page view missing')
    assert_true(eng.page_view('Simulador') is False, 'page rerun not deduped')

    # Attempt + question view dedupe.
    attempt_id = eng.start_attempt(
        attempt_type='practice',
        mode='Práctica',
        title='Self-test',
        question_count=2,
        selection_strategy='standard_v1',
    )
    q = {
        'id': 'S2-48',
        'subject': 'Física',
        'topic': 'Ondas',
        'skill': 'Relaciones',
        'subskill': 'Longitud de onda',
        'answer': 'B',
    }
    assert_true(eng.question_view(q, 1) is True, 'first question view missing')
    assert_true(eng.question_view(q, 1) is False, 'question rerun not deduped')

    # Answer normalization + answer change.
    assert_true(eng.answer_selected(q, 'B) 1,13 m') is True, 'answer selected missing')
    assert_true(eng.answer_selected(q, 'B') is False, 'same canonical answer not deduped')
    assert_true(eng.answer_selected(q, 'C) 2 m') is True, 'answer change missing')
    item = store.items[(attempt_id, 'S2-48')]
    assert_true(item['first_answer'] == 'B', 'first answer incorrect')
    assert_true(item['final_answer'] == 'C', 'final answer incorrect')
    assert_true(item['answer_change_count'] == 1, 'answer change count incorrect')

    # Flag + explanation.
    assert_true(eng.set_flag(q, True) is True, 'flag missing')
    assert_true(eng.set_flag(q, True) is False, 'flag rerun not deduped')
    assert_true(eng.explanation_viewed(q) is True, 'explanation missing')
    assert_true(eng.explanation_viewed(q) is False, 'explanation rerun not deduped')

    # Complete uses academic truth passed by app.
    detailed = [{
        'question_id': 'S2-48',
        'answered': True,
        'correct': True,
        'user_answer': 'B) 1,13 m',
        'correct_answer': 'B',
        'area': 'Física',
        'topic': 'Ondas',
        'skill': 'Relaciones',
        'subskill': 'Longitud de onda',
    }]
    assert_true(
        eng.complete_attempt(correct_count=1, total=1, percentage=100.0, detailed_items=detailed),
        'attempt completion missing',
    )
    assert_true(store.attempts[attempt_id]['status'] == 'completed', 'attempt not completed')
    assert_true(store.items[(attempt_id, 'S2-48')]['correct'] is True, 'academic truth not persisted')

    # Heartbeat + idle transition.
    base = eng._last_interaction_at
    assert_true(base is not None, 'last interaction missing')
    eng.heartbeat(now=base + timedelta(seconds=61))
    eng.heartbeat(now=base + timedelta(seconds=181))
    assert_true(eng.health_snapshot()['session_status'] == 'idle', 'idle not detected')

    # Fail-safe: analytics outage must not raise.
    bad = AnalyticsEngine(
        store=FailingStore(),
        user_id='00000000-0000-0000-0000-000000000002',
        fail_silent=True,
    )
    bad.start_session()
    bad.page_view('Simulador')
    bad.flush()
    assert_true(bad.health.store_errors >= 1, 'store error not captured')

    # End session.
    eng.end_session('logout')
    assert_true(store.sessions[sid]['status'] == 'ended', 'session not ended')

    names = [e['event_name'] for e in store.events]
    required = {
        'SESSION_STARTED', 'PAGE_VIEWED', 'PRACTICE_STARTED', 'QUESTION_VIEWED',
        'ANSWER_SELECTED', 'ANSWER_CHANGED', 'QUESTION_FLAGGED',
        'EXPLANATION_VIEWED', 'PRACTICE_COMPLETED', 'SESSION_IDLE', 'LOGOUT'
    }
    assert_true(required.issubset(set(names)), 'required event coverage incomplete')

    print('P2-M5A.3 ANALYTICS ENGINE SELF-TEST')
    print('=' * 58)
    print('[PASS] session lifecycle')
    print('[PASS] Streamlit rerun dedupe')
    print('[PASS] question view tracking')
    print('[PASS] answer canonicalization')
    print('[PASS] answer change tracking')
    print('[PASS] flag/explanation tracking')
    print('[PASS] academic truth on completion')
    print('[PASS] heartbeat + idle detection')
    print('[PASS] fail-silent outage behavior')
    print('[PASS] event coverage')
    print('DECISION : P2-M5A.3 ENGINE PASS')


if __name__ == '__main__':
    main()
