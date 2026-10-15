import hmac, logging, re, secrets, difflib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, g, request, session, redirect, url_for, render_template, jsonify, abort, flash
from werkzeug.security import check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
import evaluation, similarity, init_db
from config import load_config
from db import DB, now
from executor import Judge0Executor, ExecutorError
from security import RateLimiter, LoginThrottle

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
UNITS_MAX = 30

def create_app(overrides=None, executor=None):
    cfg = load_config(overrides)
    app = Flask(__name__)
    app.config.update(SECRET_KEY=cfg["SECRET_KEY"], MAX_CONTENT_LENGTH=512 * 1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=cfg["APP_ENV"] == "production",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8), JSON_SORT_KEYS=False)
    if cfg["TRUST_PROXY"]: app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db = DB(cfg["DATABASE_URL"]); db.create_all()
    if cfg["SEED_DEMO"]: init_db.seed(db)
    ex = executor or Judge0Executor(cfg)
    pool = None if cfg["EVAL_SYNC"] else ThreadPoolExecutor(max_workers=2)
    rl_run = RateLimiter(cfg["RATE_RUN_PER_MIN"], 60); rl_sub = RateLimiter(cfg["RATE_SUBMIT_PER_MIN"], 60)
    throttle = LoginThrottle(cfg["LOGIN_MAX_FAILS"], cfg["LOGIN_WINDOW_SEC"])
    app.extensions["codelab"] = dict(cfg=cfg, db=db, executor=ex, throttle=throttle, rl_run=rl_run, rl_sub=rl_sub)
    log = logging.getLogger("codelab")

    # ---------- helpers ----------
    def S():
        if "s" not in g: g._cm = db.session(); g.s = g._cm.__enter__()
        return g.s
    @app.teardown_appcontext
    def _close(exc):
        cm = g.pop("_cm", None)
        if cm: cm.__exit__(type(exc) if exc else None, exc, None)
    def is_api(): return request.path.startswith("/api/")
    def fail(code, msg, **extra):
        if is_api(): return jsonify(error=msg, **extra), code
        abort(code)
    def enqueue(sub_id):
        if pool is None: evaluation.evaluate(db, ex, cfg, sub_id)
        else: pool.submit(evaluation.evaluate, db, ex, cfg, sub_id)
    def login_required(role):
        def deco(f):
            @wraps(f)
            def w(*a, **k):
                if "uid" not in session:
                    return (jsonify(error="Please log in."), 401) if is_api() else redirect(url_for("login"))
                if session.get("role") != role: return fail(403, "Not allowed for your role.")
                return f(*a, **k)
            return w
        return deco
    def reap_stale():
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cfg["STALE_EVAL_MINUTES"])).strftime("%Y-%m-%d %H:%M:%S")
        S().exec("UPDATE submissions SET status='evaluation_error', error_message='Evaluation timed out; a teacher can re-evaluate.' "
                 "WHERE status IN ('submitted','evaluation_pending','evaluating') AND created_at < ?", (cutoff,))
    def recover_pending():
        """After a restart: re-queue submissions that were waiting, mark interrupted ones for retry."""
        with db.session() as s:
            ids = [r["id"] for r in s.q("SELECT id FROM submissions WHERE status IN ('submitted','evaluation_pending','evaluating')")]
            for i in ids:
                s.exec("UPDATE submissions SET status='evaluation_pending' WHERE id=?", (i,))
        for i in ids: enqueue(i)
    app.recover_pending = recover_pending

    @app.before_request
    def _csrf():
        session.setdefault("csrf", secrets.token_hex(16))
        if request.method == "POST":
            t = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
            if not hmac.compare_digest(t, session["csrf"]): return fail(400, "Invalid or missing CSRF token. Reload the page.")
    @app.after_request
    def _headers(r):
        r.headers["X-Content-Type-Options"] = "nosniff"; r.headers["X-Frame-Options"] = "DENY"
        r.headers["Referrer-Policy"] = "same-origin"
        r.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; font-src 'self' data: https://cdnjs.cloudflare.com; "
            "worker-src 'self' blob: data:; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if cfg["APP_ENV"] == "production": r.headers["Strict-Transport-Security"] = "max-age=31536000"
        return r
    @app.context_processor
    def _ctx(): return dict(csrf=session.get("csrf", ""), me=session.get("name"), role=session.get("role"), cfg=cfg)
    for code, msg in ((400, "Bad request"), (403, "You do not have access to this page"), (404, "Page not found"),
                      (413, "Request too large"), (429, "Too many requests - please wait")):
        app.register_error_handler(code, lambda e, c=code, m=msg: (jsonify(error=m), c) if is_api() else (render_template("error.html", code=c, msg=m), c))
    @app.errorhandler(500)
    def _500(e):
        log.exception("unhandled error")
        return (jsonify(error="Server error"), 500) if is_api() else (render_template("error.html", code=500, msg="Something went wrong on the server"), 500)

    # ---------- auth / health ----------
    @app.get("/health")
    def health():
        try:
            with db.session() as s: s.one("SELECT 1 AS x")
            return jsonify(status="ok", db="ok", executor_configured=ex.configured if hasattr(ex, "configured") else True)
        except Exception:
            return jsonify(status="degraded", db="error"), 503
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            uname = request.form.get("username", "").strip()[:60]
            key = f"{request.remote_addr}|{uname.lower()}"
            if throttle.blocked(key):
                flash("Too many failed attempts. Please wait a few minutes and try again."); return render_template("login.html"), 429
            u = S().one("SELECT * FROM users WHERE username=?", (uname,))
            if u and check_password_hash(u["pw_hash"], request.form.get("password", "")):
                throttle.reset(key); session.clear()
                session.update(uid=u["id"], name=u["name"], role=u["role"], csrf=secrets.token_hex(16)); session.permanent = True
                return redirect(url_for("home"))
            throttle.fail(key); flash("Invalid username or password.")
        return render_template("login.html")
    @app.post("/logout")
    def logout(): session.clear(); return redirect(url_for("login"))
    @app.get("/")
    def home():
        if "uid" not in session: return redirect(url_for("login"))
        return redirect("/student" if session["role"] == "student" else "/teacher")

    # ---------- shared builders ----------
    def event(user_id, typ, detail="", aid=None, sid=None, dedupe_min=0):
        s = S()
        if dedupe_min:
            cut = (datetime.now(timezone.utc) - timedelta(minutes=dedupe_min)).strftime("%Y-%m-%d %H:%M:%S")
            if s.one("SELECT 1 AS x FROM events WHERE user_id=? AND actor_id=? AND type=? AND COALESCE(submission_id,0)=? AND COALESCE(assignment_id,0)=? AND created_at>=?",
                     (user_id, session["uid"], typ, sid or 0, aid or 0, cut)): return
        evaluation.add_event(s, user_id, typ, detail, aid, sid, actor_id=session["uid"])
    def sub_public(sub, teacher_view=False):
        """Student-safe submission view. Hidden test inputs/expected/actual outputs are NEVER included."""
        rows = S().q("SELECT * FROM test_results WHERE submission_id=? ORDER BY position", (sub["id"],))
        res = []
        for r in rows:
            if r["hidden"] and not teacher_view:
                res.append({"position": r["position"], "hidden": True, "status": r["status"]})
            elif r["hidden"]:
                res.append({"position": r["position"], "hidden": True, "status": r["status"], "exec_status": r["exec_status"], "time": r["time"]})
            else:
                res.append({"position": r["position"], "hidden": False, "status": r["status"], "exec_status": r["exec_status"],
                            "input": r["input_snapshot"], "expected": r["expected_snapshot"], "actual": r["actual_output"],
                            "stderr": r["stderr"], "time": r["time"]})
        return {"id": sub["id"], "assignment_id": sub["assignment_id"], "attempt": sub["attempt"], "status": sub["status"],
                "created_at": sub["created_at"], "evaluated_at": sub["evaluated_at"], "passed_count": sub["passed_count"],
                "total": sub["total"], "compile_output": sub["compile_output"], "error_message": sub["error_message"], "results": res}
    def get_assignment_for_student(aid):
        a = S().one("SELECT * FROM assignments WHERE id=? AND archived=0", (aid,))
        return a or abort(404)

    # ---------- student ----------
    @app.get("/student")
    @login_required("student")
    def student_home():
        uid = session["uid"]
        rows = S().q("""SELECT a.*, (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id=a.id AND s.user_id=?) attempts,
            (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id=a.id AND s.user_id=? AND s.status='evaluated_passed') passed,
            (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id=a.id AND s.user_id=? AND s.status IN ('submitted','evaluation_pending','evaluating')) pending
            FROM assignments a WHERE a.archived=0 ORDER BY a.unit, a.id""", (uid, uid, uid))
        units = {}
        for r in rows: units.setdefault(r["unit"], []).append(r)
        recent = S().q("""SELECT e.type, e.detail, e.created_at, a.title FROM events e LEFT JOIN assignments a ON a.id=e.assignment_id
                          WHERE e.user_id=? ORDER BY e.id DESC LIMIT 8""", (uid,))
        return render_template("student_home.html", units=units, recent=recent, total=len(rows),
                               attempted=sum(1 for r in rows if r["attempts"]), passed=sum(1 for r in rows if r["passed"]),
                               pending=sum(1 for r in rows if r["pending"]))
    @app.get("/assignment/<int:aid>")
    @login_required("student")
    def workspace(aid):
        a = get_assignment_for_student(aid)
        samples = S().q("SELECT input, expected FROM tests WHERE assignment_id=? AND hidden=0 ORDER BY position,id", (aid,))  # hidden never selected
        hidden_count = S().one("SELECT COUNT(*) AS n FROM tests WHERE assignment_id=? AND hidden=1", (aid,))["n"]
        last = S().one("SELECT code FROM submissions WHERE user_id=? AND assignment_id=? ORDER BY id DESC LIMIT 1", (session["uid"], aid))
        event(session["uid"], "assignment_opened", a["title"], aid, dedupe_min=1)
        page = dict(assignment_id=aid, starter=last["code"] if last else a["starter"], paste=cfg["PASTE_DETERRENT"], max_code=cfg["MAX_CODE_BYTES"])
        return render_template("workspace.html", a=a, samples=samples, hidden_count=hidden_count, page=page)
    @app.get("/api/assignments/<int:aid>/submissions")
    @login_required("student")
    def api_history(aid):
        get_assignment_for_student(aid)
        rows = S().q("SELECT id,attempt,status,passed_count,total,created_at FROM submissions WHERE user_id=? AND assignment_id=? ORDER BY id DESC", (session["uid"], aid))
        return jsonify(submissions=rows)

    def read_json_code():
        d = request.get_json(silent=True)
        if not isinstance(d, dict): return None, None, fail(400, "Expected a JSON body.")
        code = d.get("code")
        if not isinstance(code, str) or not code.strip(): return None, None, fail(400, "The editor is empty.")
        if len(code.encode("utf-8")) > cfg["MAX_CODE_BYTES"]: return None, None, fail(413, f"Code is larger than {cfg['MAX_CODE_BYTES']} bytes.")
        return d, code, None
    @app.post("/api/assignments/<int:aid>/run")
    @login_required("student")
    def api_run(aid):
        get_assignment_for_student(aid)
        d, code, err = read_json_code()
        if err: return err
        stdin = d.get("stdin", "")
        if not isinstance(stdin, str) or len(stdin.encode("utf-8")) > cfg["MAX_STDIN_BYTES"]: return fail(413, "Input is too large.")
        if not rl_run.allow(session["uid"]): return fail(429, "You are running code too fast. Wait a moment.", retry_after=rl_run.retry_after(session["uid"]))
        event(session["uid"], "code_run", "", aid); S().commit()
        try:
            r = ex.run(code, stdin, cpu_time=cfg["CPU_TIME_LIMIT"], memory_kb=cfg["MEMORY_LIMIT_KB"])
        except ExecutorError as e:
            return jsonify(error=e.message, kind=e.kind), 503
        return jsonify({k: r[k] for k in ("kind", "status", "stdout", "stderr", "compile_output", "time", "memory")})
    @app.post("/api/assignments/<int:aid>/submit")
    @login_required("student")
    def api_submit(aid):
        a = get_assignment_for_student(aid)
        d, code, err = read_json_code()
        if err: return err
        token = d.get("client_token")
        if not isinstance(token, str) or not TOKEN_RE.match(token): return fail(400, "Missing client_token.")
        uid, s = session["uid"], S()
        dup = s.one("SELECT id,status,attempt FROM submissions WHERE user_id=? AND client_token=?", (uid, token))
        if dup: return jsonify(submission_id=dup["id"], status=dup["status"], attempt=dup["attempt"], duplicate=True), 200
        if not rl_sub.allow(uid): return fail(429, "Too many submissions. Wait a moment.", retry_after=rl_sub.retry_after(uid))
        if not s.one("SELECT 1 AS x FROM tests WHERE assignment_id=?", (aid,)): return fail(409, "This assignment has no test cases yet.")
        reap_stale(); s.commit()
        attempt = s.one("SELECT COALESCE(MAX(attempt),0)+1 AS n FROM submissions WHERE user_id=? AND assignment_id=?", (uid, aid))["n"]
        try:
            sid = s.insert("submissions", user_id=uid, assignment_id=aid, code=code, attempt=attempt, status="submitted",
                           client_token=token, created_at=now())
            evaluation.add_event(s, uid, "submission_saved", f"attempt {attempt}", aid, sid)
            evaluation.set_status(s, sid, "evaluation_pending"); s.commit()
        except db.IntegrityError:
            s.rollback()
            dup = s.one("SELECT id,status,attempt FROM submissions WHERE user_id=? AND client_token=?", (uid, token))
            if dup: return jsonify(submission_id=dup["id"], status=dup["status"], attempt=dup["attempt"], duplicate=True), 200
            return fail(409, "Your previous submission for this assignment is still being evaluated.")
        enqueue(sid)
        return jsonify(submission_id=sid, status="evaluation_pending", attempt=attempt), 202
    @app.get("/api/submissions/<int:sid>")
    @login_required("student")
    def api_submission(sid):
        sub = S().one("SELECT * FROM submissions WHERE id=? AND user_id=?", (sid, session["uid"]))  # ownership enforced in SQL
        if not sub: return fail(404, "Submission not found.")
        return jsonify(sub_public(sub))
    @app.get("/journey")
    @login_required("student")
    def journey():
        uid = session["uid"]
        subs = S().q("SELECT s.*, a.title FROM submissions s JOIN assignments a ON a.id=s.assignment_id WHERE s.user_id=? ORDER BY s.id DESC", (uid,))
        evs = S().q("SELECT e.*, a.title FROM events e LEFT JOIN assignments a ON a.id=e.assignment_id WHERE e.user_id=? ORDER BY e.id DESC LIMIT 100", (uid,))
        return render_template("journey.html", subs=subs, evs=evs)

    # ---------- reports (student: own only; teacher: any). Hidden values are never printed. ----------
    @app.get("/report/<int:sid>")
    def report(sid):
        if "uid" not in session: return redirect(url_for("login"))
        sub = S().one("SELECT * FROM submissions WHERE id=?", (sid,))
        if not sub or (session["role"] == "student" and sub["user_id"] != session["uid"]): abort(404)
        u = S().one("SELECT username,name FROM users WHERE id=?", (sub["user_id"],))
        a = S().one("SELECT title,unit FROM assignments WHERE id=?", (sub["assignment_id"],))
        attempts = S().one("SELECT COUNT(*) AS n FROM submissions WHERE user_id=? AND assignment_id=?", (sub["user_id"], sub["assignment_id"]))["n"]
        acts = S().q("SELECT type, COUNT(*) AS n FROM events WHERE user_id=? AND assignment_id=? GROUP BY type ORDER BY type", (sub["user_id"], sub["assignment_id"]))
        return render_template("report.html", sub=sub, u=u, a=a, attempts=attempts, acts=acts, pub=sub_public(sub))

    # ---------- teacher ----------
    @app.get("/teacher")
    @login_required("teacher")
    def teacher_home():
        s = S()
        counts = {r["status"]: r["n"] for r in s.q("SELECT status, COUNT(*) AS n FROM submissions GROUP BY status")}
        per = s.q("""SELECT a.id,a.title,a.unit,a.archived,
          (SELECT COUNT(DISTINCT user_id) FROM submissions x WHERE x.assignment_id=a.id) attempted,
          (SELECT COUNT(DISTINCT user_id) FROM submissions x WHERE x.assignment_id=a.id AND x.status='evaluated_passed') passed,
          (SELECT COUNT(*) FROM submissions x WHERE x.assignment_id=a.id AND x.status='evaluated_failed') failed,
          (SELECT COUNT(*) FROM submissions x WHERE x.assignment_id=a.id AND x.status IN ('submitted','evaluation_pending','evaluating')) pending,
          (SELECT COUNT(*) FROM submissions x WHERE x.assignment_id=a.id AND x.status='evaluation_error') errors,
          (SELECT COUNT(*) FROM submissions x WHERE x.assignment_id=a.id) total
          FROM assignments a ORDER BY a.archived, a.unit, a.id""")
        students = s.q("""SELECT u.id,u.name,u.username,
          (SELECT COUNT(DISTINCT assignment_id) FROM submissions x WHERE x.user_id=u.id) attempted,
          (SELECT COUNT(DISTINCT assignment_id) FROM submissions x WHERE x.user_id=u.id AND x.status='evaluated_passed') passed,
          (SELECT MAX(created_at) FROM submissions x WHERE x.user_id=u.id) last_at
          FROM users u WHERE u.role='student' ORDER BY u.username""")
        recent = s.q("""SELECT s.id,s.attempt,s.status,s.created_at,u.name student,a.title FROM submissions s
                        JOIN users u ON u.id=s.user_id JOIN assignments a ON a.id=s.assignment_id ORDER BY s.id DESC LIMIT 10""")
        pending = sum(counts.get(k, 0) for k in evaluation.ACTIVE)
        return render_template("teacher_home.html", counts=counts, per=per, students=students, recent=recent, pending=pending,
                               total=sum(counts.values()))
    @app.get("/teacher/submissions")
    @login_required("teacher")
    def teacher_submissions():
        where, args = [], []
        aid, st, stu = request.args.get("assignment", ""), request.args.get("status", ""), request.args.get("student", "")
        if aid.isdigit(): where.append("s.assignment_id=?"); args.append(int(aid))
        if st in evaluation.ALLOWED: where.append("s.status=?"); args.append(st)
        if stu.isdigit(): where.append("s.user_id=?"); args.append(int(stu))
        rows = S().q(f"""SELECT s.id,s.attempt,s.status,s.passed_count,s.total,s.created_at,u.name student,u.id uid,a.title,a.id aid FROM submissions s
                         JOIN users u ON u.id=s.user_id JOIN assignments a ON a.id=s.assignment_id
                         {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY s.id DESC LIMIT 300""", args)
        return render_template("teacher_submissions.html", rows=rows, assignments=S().q("SELECT id,title FROM assignments ORDER BY unit,id"),
                               students=S().q("SELECT id,name FROM users WHERE role='student' ORDER BY username"),
                               statuses=list(evaluation.ALLOWED), f=dict(assignment=aid, status=st, student=stu))
    def parse_form():
        f = request.form
        a = {k: f.get(k, "").strip() for k in ("title", "unit", "description", "input_format", "output_format", "constraints_text", "deadline", "starter")}
        a["compare_mode"] = f.get("compare_mode", "lenient") if f.get("compare_mode") in ("lenient", "exact") else "lenient"
        try: a["time_limit"] = min(max(float(f.get("time_limit", "2") or 2), 0.5), 5.0)
        except ValueError: a["time_limit"] = 2.0
        ins, exps, hid = f.getlist("t_input"), f.getlist("t_expected"), f.getlist("t_hidden")
        tests = [(i.replace("\r\n", "\n"), e.replace("\r\n", "\n").rstrip("\n"), 1 if h == "1" else 0) for i, e, h in zip(ins, exps, hid) if e.strip() != "" or i.strip() != ""]
        msg = None
        if not a["title"] or len(a["title"]) > 120: msg = "Title is required (max 120 characters)."
        elif not a["unit"] or len(a["unit"]) > 80: msg = "Unit is required (max 80 characters)."
        elif not a["description"] or len(a["description"]) > 8000: msg = "Description is required (max 8000 characters)."
        elif a["deadline"] and not re.match(r"^\d{4}-\d{2}-\d{2}$", a["deadline"]): msg = "Deadline must be a date (YYYY-MM-DD)."
        elif not tests: msg = "Add at least one test case."
        elif len(tests) > UNITS_MAX: msg = f"At most {UNITS_MAX} test cases."
        elif any(e == "" for _, e, _ in tests): msg = "Every test case needs an expected output."
        elif sum(len(i) + len(e) for i, e, _ in tests) > 200000: msg = "Test data is too large."
        if not a["starter"]: a["starter"] = "#include <iostream>\nusing namespace std;\n\nint main() {\n    // Write your code here\n    return 0;\n}\n"
        return a, tests, msg
    @app.route("/teacher/assignment/new", methods=["GET", "POST"])
    @app.route("/teacher/assignment/<int:aid>/edit", methods=["GET", "POST"])
    @login_required("teacher")
    def assignment_form(aid=None):
        s = S(); cur = s.one("SELECT * FROM assignments WHERE id=?", (aid,)) if aid else {}
        if aid and not cur: abort(404)
        tests = [(t["input"], t["expected"], t["hidden"]) for t in s.q("SELECT * FROM tests WHERE assignment_id=? ORDER BY position,id", (aid,))] if aid else [("", "", 0)]
        if request.method == "POST":
            a, tests_new, msg = parse_form()
            if msg: flash(msg); cur = a; tests = tests_new or tests
            else:
                if aid:
                    s.exec("UPDATE assignments SET title=?,unit=?,description=?,input_format=?,output_format=?,constraints_text=?,deadline=?,starter=?,compare_mode=?,time_limit=? WHERE id=?",
                           (a["title"], a["unit"], a["description"], a["input_format"], a["output_format"], a["constraints_text"], a["deadline"], a["starter"], a["compare_mode"], a["time_limit"], aid))
                    s.exec("DELETE FROM tests WHERE assignment_id=?", (aid,))
                else:
                    aid = s.insert("assignments", created_at=now(), **a)
                for pos, (i, e, h) in enumerate(tests_new, 1): s.insert("tests", assignment_id=aid, position=pos, input=i, expected=e, hidden=h)
                flash("Assignment saved. Existing submissions are not re-evaluated automatically."); return redirect(url_for("teacher_home"))
        return render_template("assign_form.html", a=cur, tests=tests or [("", "", 0)], aid=aid)
    @app.post("/teacher/assignment/<int:aid>/delete")
    @login_required("teacher")
    def assignment_delete(aid):
        s = S(); a = s.one("SELECT id FROM assignments WHERE id=?", (aid,)) or abort(404)
        if s.one("SELECT 1 AS x FROM submissions WHERE assignment_id=?", (aid,)):
            s.exec("UPDATE assignments SET archived=1 WHERE id=?", (aid,)); flash("Assignment has submissions, so it was archived (hidden from students) instead of deleted.")
        else:
            s.exec("DELETE FROM assignments WHERE id=?", (aid,)); flash("Assignment deleted.")
        return redirect(url_for("teacher_home"))
    @app.post("/teacher/assignment/<int:aid>/restore")
    @login_required("teacher")
    def assignment_restore(aid):
        S().exec("UPDATE assignments SET archived=0 WHERE id=?", (aid,)); flash("Assignment restored."); return redirect(url_for("teacher_home"))

    @app.get("/teacher/submission/<int:sid>")
    @login_required("teacher")
    def teacher_submission(sid):
        s = S(); sub = s.one("SELECT * FROM submissions WHERE id=?", (sid,)) or abort(404)
        u = s.one("SELECT id,username,name FROM users WHERE id=?", (sub["user_id"],)); a = s.one("SELECT * FROM assignments WHERE id=?", (sub["assignment_id"],))
        event(sub["user_id"], "submission_reviewed", "", sub["assignment_id"], sid, dedupe_min=10)
        results = s.q("SELECT * FROM test_results WHERE submission_id=? ORDER BY position", (sid,))
        others = s.q("SELECT id,attempt,status,created_at FROM submissions WHERE user_id=? AND assignment_id=? AND id<>? ORDER BY id", (sub["user_id"], sub["assignment_id"], sid))
        evs = s.q("SELECT * FROM events WHERE user_id=? AND assignment_id=? ORDER BY id", (sub["user_id"], sub["assignment_id"]))
        latest = s.q("""SELECT x.id, x.code, x.user_id, u.name FROM submissions x JOIN users u ON u.id=x.user_id WHERE x.assignment_id=? AND x.user_id<>?
                        AND x.id=(SELECT MAX(id) FROM submissions y WHERE y.user_id=x.user_id AND y.assignment_id=x.assignment_id)""", (sub["assignment_id"], sub["user_id"]))
        sims = []
        for o in latest:
            r = similarity.compare(sub["code"], o["code"], cfg["SIM_MIN_TOKENS"], a["starter"])
            sims.append(dict(r, other_id=o["id"], other_name=o["name"], flag=similarity.flagged(r, cfg["SIM_THRESHOLD"])))
        sims.sort(key=lambda r: -r["structure_score"])
        return render_template("sub_detail.html", sub=sub, u=u, a=a, results=results, others=others, evs=evs, sims=sims[:5], threshold=cfg["SIM_THRESHOLD"])
    @app.post("/teacher/submission/<int:sid>/reevaluate")
    @login_required("teacher")
    def reevaluate(sid):
        s = S(); sub = s.one("SELECT * FROM submissions WHERE id=?", (sid,)) or abort(404)
        if sub["status"] in ("submitted", "evaluation_pending", "evaluating"): flash("This submission is already being evaluated.")
        elif evaluation.set_status(s, sid, "evaluation_pending", error_message=""):
            evaluation.add_event(s, sub["user_id"], "evaluation_requested", "re-evaluation", sub["assignment_id"], sid, actor_id=session["uid"]); s.commit(); enqueue(sid); flash("Re-evaluation started.")
        return redirect(url_for("teacher_submission", sid=sid))
    @app.get("/teacher/compare/<int:a_id>/<int:b_id>")
    @login_required("teacher")
    def compare(a_id, b_id):
        s = S()
        q = "SELECT s.*, u.name, a.starter FROM submissions s JOIN users u ON u.id=s.user_id JOIN assignments a ON a.id=s.assignment_id WHERE s.id=?"
        A, B = s.one(q, (a_id,)) or abort(404), s.one(q, (b_id,)) or abort(404)
        if A["assignment_id"] != B["assignment_id"]: abort(400)
        r = similarity.compare(A["code"], B["code"], cfg["SIM_MIN_TOKENS"], A["starter"])
        diff = list(difflib.unified_diff(A["code"].splitlines(), B["code"].splitlines(), f"#{A['id']} {A['name']}", f"#{B['id']} {B['name']}", lineterm=""))
        return render_template("compare.html", A=A, B=B, r=r, diff=diff, same_student=A["user_id"] == B["user_id"], threshold=cfg["SIM_THRESHOLD"])
    @app.get("/teacher/similarity/<int:aid>")
    @login_required("teacher")
    def similarity_page(aid):
        s = S(); a = s.one("SELECT * FROM assignments WHERE id=?", (aid,)) or abort(404)
        subs = s.q("""SELECT x.id,x.user_id,x.code,u.name FROM submissions x JOIN users u ON u.id=x.user_id WHERE x.assignment_id=?
                      AND x.id=(SELECT MAX(id) FROM submissions y WHERE y.user_id=x.user_id AND y.assignment_id=x.assignment_id)""", (aid,))
        pairs = []
        for i in range(len(subs)):
            for j in range(i + 1, len(subs)):
                r = similarity.compare(subs[i]["code"], subs[j]["code"], cfg["SIM_MIN_TOKENS"], a["starter"])
                pairs.append(dict(r, a=subs[i], b=subs[j], flag=similarity.flagged(r, cfg["SIM_THRESHOLD"])))
        pairs.sort(key=lambda p: -p["structure_score"])
        return render_template("similarity.html", a=a, pairs=pairs[:100], n=len(subs), threshold=cfg["SIM_THRESHOLD"])
    @app.route("/teacher/service", methods=["GET", "POST"])
    @login_required("teacher")
    def service():
        info, check = ex.describe() if hasattr(ex, "describe") else {"configured": True}, None
        if request.method == "POST":
            try:
                v = ex.verify(); r = ex.run('#include <iostream>\nint main(){std::cout<<"hello";}', "")
                check = dict(ok=r["kind"] == "ok" and r["stdout"].strip() == "hello", language=v["language"], detail=f'{r["status"]}, output={r["stdout"].strip()!r}')
            except ExecutorError as e: check = dict(ok=False, language="", detail=f"{e.message} ({e.kind})")
        return render_template("service.html", info=info, check=check)
    return app

if __name__ == "__main__":
    create_app().run(debug=False)
