"""Submission evaluation: output comparison, per-test results and the status state machine."""
import logging
from db import now
from executor import ExecutorError

log = logging.getLogger("codelab.eval")
ACTIVE = ("submitted", "evaluation_pending", "evaluating")
TERMINAL = ("evaluated_passed", "evaluated_failed", "evaluation_error")
ALLOWED = {
    "submitted": {"evaluation_pending"},
    "evaluation_pending": {"evaluating", "evaluation_error"},
    "evaluating": {"evaluated_passed", "evaluated_failed", "evaluation_error"},
    "evaluation_error": {"evaluation_pending"},
    "evaluated_passed": {"evaluation_pending"},   # teacher-triggered re-evaluation
    "evaluated_failed": {"evaluation_pending"},
}
MAX_STORED = 20_000

def normalize(text, mode="lenient"):
    """Comparison policy. Both modes convert CRLF/CR to LF.
    lenient: also removes trailing spaces on every line and trailing blank lines.
    exact:   keeps all spaces; only ONE final newline is optional.
    Internal characters/spacing are never altered."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if mode == "exact":
        return t[:-1] if t.endswith("\n") else t
    return "\n".join(l.rstrip() for l in t.split("\n")).rstrip("\n")

def set_status(s, sub_id, new, **fields):
    """Atomic compare-and-set transition. Returns False if the transition is not allowed."""
    cur = s.one("SELECT status FROM submissions WHERE id=?", (sub_id,))
    if not cur or new not in ALLOWED.get(cur["status"], ()): return False
    sets = ",".join(f"{k}=?" for k in fields)
    n = s.exec(f"UPDATE submissions SET status=?{',' + sets if sets else ''} WHERE id=? AND status=?",
               (new, *fields.values(), sub_id, cur["status"]))
    return n == 1

def add_event(s, user_id, typ, detail="", assignment_id=None, submission_id=None, actor_id=None):
    s.insert("events", user_id=user_id, actor_id=actor_id if actor_id is not None else user_id, assignment_id=assignment_id,
             submission_id=submission_id, type=typ, detail=detail[:200], created_at=now())

def evaluate(db, executor, cfg, sub_id):
    """Run every test for one submission. Service failures NEVER become 'failed' answers."""
    try:
        with db.session() as s:
            sub = s.one("SELECT * FROM submissions WHERE id=?", (sub_id,))
            if not sub: return
            a = s.one("SELECT * FROM assignments WHERE id=?", (sub["assignment_id"],))
            tests = s.q("SELECT * FROM tests WHERE assignment_id=? ORDER BY position,id", (a["id"],))
            if not set_status(s, sub_id, "evaluating", error_message="", compile_output=""): return
            s.exec("DELETE FROM test_results WHERE submission_id=?", (sub_id,))
        passed = 0
        for pos, t in enumerate(tests, 1):
            try:
                r = executor.run(sub["code"], t["input"], cpu_time=a["time_limit"], memory_kb=cfg.get("MEMORY_LIMIT_KB"))
            except ExecutorError as e:
                return _finish(db, sub, "evaluation_error", passed, len(tests), error=f"Evaluation incomplete: {e.message} ({e.kind})")
            if r["kind"] == "compile_error":
                with db.session() as s: s.exec("DELETE FROM test_results WHERE submission_id=?", (sub_id,))
                return _finish(db, sub, "evaluated_failed", 0, len(tests), compile=r["compile_output"] or r["message"])
            if r["kind"] == "ok":
                ok = normalize(r["stdout"], a["compare_mode"]) == normalize(t["expected"], a["compare_mode"])
                status = "passed" if ok else "failed"
            else:
                status = "error"
            passed += status == "passed"
            with db.session() as s:
                s.insert("test_results", submission_id=sub_id, test_id=t["id"], position=pos, hidden=t["hidden"], status=status,
                         exec_status=r["status"] if r["kind"] != "ok" else "Completed", input_snapshot=t["input"][:MAX_STORED],
                         expected_snapshot=t["expected"][:MAX_STORED], actual_output=r["stdout"][:MAX_STORED],
                         stderr=r["stderr"][:MAX_STORED], time=r["time"], memory=r["memory"])
        final = "evaluated_passed" if tests and passed == len(tests) else "evaluated_failed"
        _finish(db, sub, final, passed, len(tests))
    except Exception:  # never leave a submission stuck in 'evaluating'
        log.exception("evaluation crashed for submission %s", sub_id)
        try:
            with db.session() as s:
                set_status(s, sub_id, "evaluation_error", error_message="Internal evaluation error. A teacher can re-evaluate.")
        except Exception: log.exception("could not record evaluation_error")

def _finish(db, sub, status, passed, total, error="", compile=""):
    with db.session() as s:
        if set_status(s, sub["id"], status, passed_count=passed, total=total, error_message=error, compile_output=compile[:MAX_STORED],
                      evaluated_at=now() if status != "evaluation_error" else None):
            add_event(s, sub["user_id"], "evaluation_completed", status, sub["assignment_id"], sub["id"])
