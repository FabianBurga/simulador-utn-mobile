from __future__ import annotations
from pathlib import Path
import ast
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analytics_integration import (
    ENGINE_KEY, ENGINE_USER_KEY, analytics_abandon_attempt,
    analytics_answer_selected, analytics_complete_attempt, analytics_logout,
    analytics_page_view, analytics_question_view, analytics_set_flag,
    analytics_start_attempt, ensure_analytics,
)

class FakeState(dict):
    def __getattr__(self, name):
        try: return self[name]
        except KeyError as exc: raise AttributeError(name) from exc
    def __setattr__(self, name, value): self[name] = value

class FakeSt:
    def __init__(self):
        self.session_state = FakeState()
        self.secrets = {"mobile": {"backend": "local"}}

def check(v, m):
    if not v: raise AssertionError(m)

def static_contract():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    mobile = (ROOT / "mobile_backend.py").read_text(encoding="utf-8")
    required = [
        "P2-M5A.4 - ANALYTICS INSTRUMENTATION", "ensure_analytics(",
        "analytics_page_view(st, page)", "analytics_start_attempt(",
        "analytics_question_view(st, q, idx + 1)",
        "analytics_answer_selected(st, q, ans)", "analytics_set_flag(",
        "analytics_complete_attempt(",
        "analytics_abandon_attempt(st, reason=\"reset_exam\")",
        "on_logout=lambda: analytics_logout(st)",
    ]
    missing = [x for x in required if x not in app]
    check(not missing, "missing instrumentation: " + ", ".join(missing))
    check("on_logout=None" in mobile and "on_logout()" in mobile, "logout callback missing")
    tree = ast.parse(app)
    def is_q_answer(node):
        return (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "q" and isinstance(node.slice, ast.Constant)
                and node.slice.value == "answer")
    fragile = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            ops = [node.left, *node.comparators]
            names = {x.id for x in ops if isinstance(x, ast.Name) and x.id in {"user", "ans"}}
            if names and any(is_q_answer(x) for x in ops): fragile.append(node.lineno)
    check(not fragile, "M4.3 fragile scoring returned")

def runtime_contract():
    st = FakeSt(); st.session_state["p2m_session_id"] = "SELFTEST"
    ctx = {"user_id": "00000000-0000-0000-0000-000000000111", "user_code": "SELFTEST", "backend": "local"}
    eng = ensure_analytics(st, ctx)
    check(eng is not None, "engine init")
    check(st.session_state.get(ENGINE_KEY) is eng, "engine state")
    check(st.session_state.get(ENGINE_USER_KEY) == ctx["user_id"], "user binding")
    check(analytics_page_view(st, "Simulador") is True, "page")
    check(analytics_page_view(st, "Simulador") is False, "page dedupe")
    qs = [
        {"id":"S2-48","subject":"Física","topic":"Ondas","answer":"B"},
        {"id":"S2-49","subject":"Física","topic":"Cinemática","answer":"C"},
    ]
    check(analytics_start_attempt(st, mode="Práctica", title="Selftest", questions=qs), "attempt")
    st.session_state["exam_questions"] = qs; st.session_state["mode"] = "Práctica"; st.session_state["exam_title"] = "Selftest"
    check(analytics_question_view(st, qs[0], 1) is True, "question")
    check(analytics_question_view(st, qs[0], 1) is False, "question dedupe")
    check(analytics_answer_selected(st, qs[0], "B) 1,13 m") is True, "answer")
    check(analytics_answer_selected(st, qs[0], "B") is False, "canonical dedupe")
    check(analytics_answer_selected(st, qs[0], "C) 2 m") is True, "answer change")
    check(analytics_set_flag(st, qs[0], True) is True, "flag")
    check(analytics_set_flag(st, qs[0], True) is False, "flag dedupe")
    check(analytics_complete_attempt(st, qs=qs, answers={"S2-48":"B","S2-49":"C"}, correct=2, total=2, pct=100.0), "complete")
    check(eng.attempt_id is None, "attempt clear")
    analytics_start_attempt(st, mode="Examen rápido", title="Selftest2", questions=qs)
    check(analytics_abandon_attempt(st, "reset_exam") is True, "abandon")
    analytics_logout(st); check(ENGINE_KEY not in st.session_state, "logout clear")

def main():
    static_contract(); runtime_contract()
    print("P2-M5A.4 INSTRUMENTATION SELF-TEST")
    print("=" * 60)
    for name in ["static contract","M4.3 scoring guard","session","page dedupe","attempt","question","answer/change","flag","completion","abandon","logout"]:
        print("[PASS] " + name)
    print("DECISION : P2-M5A.4 INSTRUMENTATION PASS")
if __name__ == "__main__": main()
