"""Optional LOCAL executor (EXECUTOR=local): compiles and runs C++ with the g++ installed on this server.

SECURITY: this is weaker than a Judge0 sandbox. Student code runs on the web server's machine, as an unprivileged user
(requires the app to run as root inside a container so it can drop privileges), with CPU / memory / file-size / process limits,
a wall-clock timeout, an empty environment and a private working directory. It does NOT block network access and is not a
kernel-level sandbox. Use it only for a closed demo. Same interface as Judge0Executor."""
import hashlib, os, pwd, resource, shutil, signal, subprocess, tempfile, threading, time
from executor import ExecutorError, MAX_OUT

SIGNAMES = {6: "SIGABRT", 7: "SIGBUS", 8: "SIGFPE", 11: "SIGSEGV", 4: "SIGILL"}

class LocalExecutor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.sem = threading.Semaphore(cfg.get("LOCAL_EXEC_CONCURRENCY", 2))
        self.root = os.path.join(tempfile.gettempdir(), "codelab-run"); os.makedirs(self.root, exist_ok=True); os.chmod(self.root, 0o755)
        self.lock, self.cache = threading.Lock(), {}
        self.uid = None
        if os.geteuid() == 0:
            try: self.uid = pwd.getpwnam("nobody").pw_uid
            except KeyError: self.uid = 65534
    # ---- status ----
    @property
    def isolated(self): return self.uid is not None
    @property
    def configured(self):
        return shutil.which("g++") is not None and (self.isolated or bool(self.cfg.get("LOCAL_EXEC_ALLOW_SAME_USER")))
    def describe(self):
        return {"configured": self.configured, "host": "this server (local g++)", "language_id": "-", "auth": "none", "mode": "local",
                "isolation": "runs as user 'nobody' + rlimits" if self.isolated else "SAME USER as the web app + rlimits (weak)"}
    def verify(self):
        self._need_ready()
        v = subprocess.run(["g++", "--version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
        return {"language_id": "local", "language": "C++ (" + v + ")", "cpp_languages": [v]}
    def _need_ready(self):
        if shutil.which("g++") is None: raise ExecutorError("not_configured", "g++ is not installed on this server (deploy with the Dockerfile).")
        if not self.configured: raise ExecutorError("not_configured", "Local execution needs the app to run as root in a container so it can drop privileges.")
    # ---- internals ----
    def _chown(self, path):
        if self.uid is not None: os.chown(path, self.uid, self.uid)
    def _limits(self, cpu, mem_bytes, nproc, fsize=1_000_000):
        def f():
            resource.setrlimit(resource.RLIMIT_CPU, (int(cpu) + 1, int(cpu) + 2))
            if mem_bytes: resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize)); resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64)); resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        return f
    def _popen(self, argv, cwd, stdin, stdout, stderr, cpu, mem_bytes, nproc):
        kw = dict(user=self.uid, group=self.uid, extra_groups=[]) if self.uid is not None else {}
        return subprocess.Popen(argv, cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr, start_new_session=True,
                                env={"PATH": "/usr/bin:/bin", "HOME": cwd, "TMPDIR": cwd, "LANG": "C.UTF-8"},
                                preexec_fn=self._limits(cpu, mem_bytes, nproc), **kw)
    @staticmethod
    def _killgroup(p):
        try: os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
    def _compile(self, code):
        h = hashlib.sha256(code.encode("utf-8")).hexdigest()[:24]
        with self.lock:
            e = self.cache.get(h)
            if e and os.path.exists(e["exe"]): e["t"] = time.time(); return e, None
        d = tempfile.mkdtemp(dir=self.root); os.chmod(d, 0o755); self._chown(d)
        src = os.path.join(d, "main.cpp")
        with open(src, "w", encoding="utf-8") as f: f.write(code)
        os.chmod(src, 0o644)
        try:
            with open(os.path.join(d, "cerr.txt"), "wb") as ferr:
                p = self._popen(["g++", "-std=c++17", "-O1", "-pipe", "-o", "a.out", "main.cpp"], d, subprocess.DEVNULL, subprocess.DEVNULL, ferr,
                                self.cfg.get("LOCAL_COMPILE_TIMEOUT", 60), 1_500_000_000, 128)
                try: p.wait(timeout=self.cfg.get("LOCAL_COMPILE_TIMEOUT", 60))
                except subprocess.TimeoutExpired:
                    self._killgroup(p); p.wait(); raise ExecutorError("timeout", "Compilation took too long.")
                self._killgroup(p)
            if p.returncode != 0:
                with open(os.path.join(d, "cerr.txt"), "rb") as f: msg = f.read(8000).decode("utf-8", "replace")
                shutil.rmtree(d, ignore_errors=True); return None, msg.replace(d + "/", "")
        except BaseException:
            shutil.rmtree(d, ignore_errors=True); raise
        e = {"dir": d, "exe": os.path.join(d, "a.out"), "t": time.time()}
        with self.lock:
            self.cache[h] = e
            old = sorted(self.cache.items(), key=lambda kv: kv[1]["t"])
            while len(self.cache) > self.cfg.get("LOCAL_CACHE_SIZE", 40) or (old and time.time() - old[0][1]["t"] > 1200 and len(self.cache) > 1):
                k, v = old.pop(0); self.cache.pop(k, None); shutil.rmtree(v["dir"], ignore_errors=True)
        return e, None
    # ---- public ----
    def run(self, code, stdin="", cpu_time=None, memory_kb=None):
        self._need_ready()
        cpu = float(cpu_time or self.cfg.get("CPU_TIME_LIMIT", 2)); mem = int((memory_kb or self.cfg.get("MEMORY_LIMIT_KB", 128000)) * 1024)
        if not self.sem.acquire(timeout=60): raise ExecutorError("queue_full", "The server is busy running other programs; try again.")
        try:
            e, cerr = self._compile(code)
            if e is None:
                return {"kind": "compile_error", "status": "Compilation Error", "status_id": 6, "stdout": "", "stderr": "", "compile_output": cerr,
                        "time": None, "memory": None, "message": ""}
            rd = tempfile.mkdtemp(dir=e["dir"]); os.chmod(rd, 0o755); self._chown(rd)
            try:
                paths = {k: os.path.join(rd, k) for k in ("in", "out", "err")}
                with open(paths["in"], "wb") as f: f.write((stdin or "").encode("utf-8"))
                for k in paths: os.chmod(paths[k], 0o666) if os.path.exists(paths[k]) else None
                with open(paths["in"], "rb") as fin, open(paths["out"], "wb") as fout, open(paths["err"], "wb") as ferr:
                    t0 = time.monotonic()
                    p = self._popen([e["exe"]], rd, fin, fout, ferr, cpu, mem, 64)
                    timed_out = False
                    try: p.wait(timeout=cpu * 2 + 1)
                    except subprocess.TimeoutExpired: timed_out = True; self._killgroup(p); p.wait()
                    self._killgroup(p); dt = time.monotonic() - t0
                out = open(paths["out"], "rb").read(MAX_OUT).decode("utf-8", "replace"); err = open(paths["err"], "rb").read(20000).decode("utf-8", "replace")
            finally:
                shutil.rmtree(rd, ignore_errors=True)
            rc = p.returncode; r = {"stdout": out, "stderr": err, "compile_output": "", "time": round(dt, 3), "memory": None, "message": ""}
            if timed_out or rc in (-24,) or (rc == -9 and dt >= cpu * 0.9): return dict(r, kind="timeout", status="Time Limit Exceeded", status_id=5)
            if rc == 0: return dict(r, kind="ok", status="Accepted", status_id=3)
            if rc < 0 and -rc == 6 and "bad_alloc" in err: return dict(r, kind="memory_limit", status="Memory Limit Exceeded", status_id=7)
            sig = -rc if rc < 0 else None
            name = f"Runtime Error ({SIGNAMES.get(sig, 'signal ' + str(sig))})" if sig else "Runtime Error (NZEC)"
            return dict(r, kind="runtime_error", status=name, status_id=11 if sig is None else 7)
        finally:
            self.sem.release()
