"""Judge0-compatible execution adapter.

Flow: POST /submissions (wait=false) -> token -> poll GET /submissions/{token} until a terminal status.
Student code is never executed by this application; it is sent to the configured service.
Secrets (API key) stay on the server and are never put in error messages."""
import base64, json, time, urllib.request, urllib.error, urllib.parse, socket

class ExecutorError(Exception):
    """The execution SERVICE failed (not a student mistake). kind is one of:
    not_configured, auth, rate_limit, queue_full, network, timeout, malformed, bad_request, internal"""
    def __init__(self, kind, message):
        super().__init__(message); self.kind, self.message = kind, message

MAX_OUT = 100_000
def _b64(s): return base64.b64encode(s.encode("utf-8")).decode("ascii")
def _unb64(s):
    if s is None: return ""
    try: return base64.b64decode(s).decode("utf-8", "replace")[:MAX_OUT]
    except Exception: raise ExecutorError("malformed", "Execution service returned invalid base64 data")

class Judge0Executor:
    def __init__(self, cfg, sleep=time.sleep, clock=time.monotonic):
        self.cfg, self.sleep, self.clock = cfg, sleep, clock
    @property
    def configured(self): return bool(self.cfg.get("JUDGE0_URL"))
    def describe(self):
        """Safe-to-display summary (never includes the API key)."""
        host = urllib.parse.urlparse(self.cfg.get("JUDGE0_URL", "")).netloc
        return {"configured": self.configured, "host": host, "language_id": self.cfg["JUDGE0_LANGUAGE_ID"],
                "auth": "RapidAPI key" if self.cfg.get("JUDGE0_API_HOST") and self.cfg.get("JUDGE0_API_KEY")
                        else ("auth token" if self.cfg.get("JUDGE0_API_KEY") else "none")}
    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        key, host = self.cfg.get("JUDGE0_API_KEY"), self.cfg.get("JUDGE0_API_HOST")
        if key and host: h["X-RapidAPI-Key"], h["X-RapidAPI-Host"] = key, host
        elif key: h[self.cfg.get("JUDGE0_AUTH_HEADER") or "X-Auth-Token"] = key
        return h
    def _request(self, method, path, payload=None):
        if not self.configured:
            raise ExecutorError("not_configured", "Code execution is not configured (JUDGE0_URL is empty).")
        url = self.cfg["JUDGE0_URL"] + path
        data = json.dumps(payload).encode() if payload is not None else None
        last = None
        for attempt in range(self.cfg["JUDGE0_MAX_RETRIES"] + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
                with urllib.request.urlopen(req, timeout=self.cfg["JUDGE0_HTTP_TIMEOUT"]) as r:
                    body = r.read()
                try: return json.loads(body.decode("utf-8"))
                except ValueError: raise ExecutorError("malformed", "Execution service returned a response that is not JSON")
            except urllib.error.HTTPError as e:
                text = e.read()[:300].decode("utf-8", "replace").lower() if hasattr(e, "read") else ""
                if e.code in (401, 403): raise ExecutorError("auth", "Execution service rejected the credentials (check JUDGE0_API_KEY / host).")
                if e.code in (400, 422): raise ExecutorError("bad_request", "Execution service rejected the request (check language id / limits): " + text[:120])
                if e.code == 429:
                    last = ExecutorError("rate_limit", "Execution service rate limit reached; try again shortly.")
                    ra = e.headers.get("Retry-After") if e.headers else None
                    delay = min(float(ra), 10) if ra and ra.replace(".", "", 1).isdigit() else None
                elif e.code in (502, 503, 504) or "queue" in text:
                    last = ExecutorError("queue_full" if "queue" in text else "internal", "Execution service is busy or temporarily unavailable.")
                    delay = None
                else:
                    raise ExecutorError("internal", f"Execution service returned HTTP {e.code}.")
            except ExecutorError: raise
            except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
                last = ExecutorError("network", "Could not reach the execution service."); delay = None
            if attempt < self.cfg["JUDGE0_MAX_RETRIES"]:
                self.sleep(delay if delay is not None else min(0.5 * 2 ** attempt, 8))
        raise last
    def run(self, code, stdin="", cpu_time=None, memory_kb=None):
        payload = {"source_code": _b64(code), "language_id": self.cfg["JUDGE0_LANGUAGE_ID"], "stdin": _b64(stdin or ""),
                   "cpu_time_limit": float(cpu_time or self.cfg["CPU_TIME_LIMIT"])}
        mem = memory_kb or self.cfg.get("MEMORY_LIMIT_KB")
        if mem: payload["memory_limit"] = int(mem)
        use_wait = self.cfg.get("JUDGE0_USE_WAIT")   # 1 API call per run instead of POST + polls (saves hosted-API quota)
        try:
            r = self._request("POST", "/submissions?base64_encoded=true&wait=" + ("true" if use_wait else "false"), payload)
        except ExecutorError as e:
            if use_wait and e.kind == "bad_request":  # this host does not allow wait=true: fall back to async polling
                r = self._request("POST", "/submissions?base64_encoded=true&wait=false", payload)
            else: raise
        st0 = r.get("status") if isinstance(r, dict) else None
        if isinstance(st0, dict) and isinstance(st0.get("id"), int) and st0["id"] not in (1, 2):
            return self._result(r, st0["id"], st0.get("description", ""), mem)
        token = r.get("token") if isinstance(r, dict) else None
        if not token or not isinstance(token, str): raise ExecutorError("malformed", "Execution service did not return a submission token")
        deadline = self.clock() + self.cfg["JUDGE0_DEADLINE"]; interval = self.cfg["JUDGE0_POLL_INTERVAL"]
        while True:
            if self.clock() > deadline:
                raise ExecutorError("timeout", "Execution service did not finish within the allowed time.")
            self.sleep(interval); interval = min(interval * 1.4, 2.5)
            d = self._request("GET", "/submissions/" + urllib.parse.quote(token) + "?base64_encoded=true")
            st = d.get("status") if isinstance(d, dict) else None
            sid = st.get("id") if isinstance(st, dict) else None
            if not isinstance(sid, int): raise ExecutorError("malformed", "Execution service returned an unexpected status format")
            if sid in (1, 2): continue
            return self._result(d, sid, st.get("description", ""), mem)
    def _result(self, d, sid, desc, mem_limit):
        out, err, comp = _unb64(d.get("stdout")), _unb64(d.get("stderr")), _unb64(d.get("compile_output"))
        try: t = float(d["time"]) if d.get("time") is not None else None
        except (TypeError, ValueError): t = None
        m = d.get("memory") if isinstance(d.get("memory"), int) else None
        r = {"status_id": sid, "status": desc, "stdout": out, "stderr": err, "compile_output": comp,
             "time": t, "memory": m, "message": _unb64(d.get("message"))}
        if sid in (3, 4): r["kind"] = "ok"
        elif sid == 5: r["kind"] = "timeout"
        elif sid == 6: r["kind"] = "compile_error"
        elif 7 <= sid <= 12:
            oom = "bad_alloc" in err or (mem_limit and m and m >= mem_limit * 0.95)
            r["kind"] = "memory_limit" if oom else "runtime_error"
        elif sid == 13: raise ExecutorError("internal", "Execution service reported an internal error.")
        else: raise ExecutorError("internal", f"Execution service returned status {sid} ({desc}).")
        return r
    def verify(self):
        """Check that the configured language id exists and report the compiler name."""
        langs = self._request("GET", "/languages")
        if not isinstance(langs, list): raise ExecutorError("malformed", "Unexpected /languages response")
        want = self.cfg["JUDGE0_LANGUAGE_ID"]
        found = next((l for l in langs if isinstance(l, dict) and l.get("id") == want), None)
        cpp = [f'{l["id"]}: {l["name"]}' for l in langs if isinstance(l, dict) and "C++" in str(l.get("name", ""))]
        if not found: raise ExecutorError("bad_request", f"Language id {want} not found. C++ ids on this service: {cpp}")
        return {"language_id": want, "language": found.get("name"), "cpp_languages": cpp}
