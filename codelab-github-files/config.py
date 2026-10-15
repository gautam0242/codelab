"""Configuration. All settings come from environment variables (or a local .env file)."""
import os, secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def _s(name, default=""):
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v.strip()
def _i(name, default): return int(_s(name, default))
def _f(name, default): return float(_s(name, default))
def _b(name, default): return _s(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")

def _dev_secret():
    """Development only: create a secret once and reuse it, so sessions survive restarts."""
    p = ROOT / "instance" / "secret.key"
    if not p.exists():
        p.parent.mkdir(exist_ok=True)
        p.write_text(secrets.token_hex(32))
    return p.read_text().strip()

def load_config(overrides=None):
    env = _s("APP_ENV", "development").lower()
    secret = _s("SECRET_KEY")
    if not secret:
        if env == "production":
            raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")
        secret = _dev_secret()
    url = _s("DATABASE_URL", "sqlite:///" + str(ROOT / "instance" / "codelab.db"))
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    c = dict(
        APP_ENV=env, SECRET_KEY=secret, DATABASE_URL=url,
        TRUST_PROXY=_b("TRUST_PROXY", env == "production"),
        # --- Judge0-compatible execution service ---
        JUDGE0_URL=_s("JUDGE0_URL").rstrip("/"), JUDGE0_API_KEY=_s("JUDGE0_API_KEY"),
        JUDGE0_API_HOST=_s("JUDGE0_API_HOST"), JUDGE0_AUTH_HEADER=_s("JUDGE0_AUTH_HEADER", "X-Auth-Token"),
        JUDGE0_LANGUAGE_ID=_i("JUDGE0_LANGUAGE_ID", 54), JUDGE0_USE_WAIT=_b("JUDGE0_USE_WAIT", False),
        JUDGE0_DEADLINE=_f("JUDGE0_DEADLINE", 45), JUDGE0_POLL_INTERVAL=_f("JUDGE0_POLL_INTERVAL", 0.7),
        JUDGE0_HTTP_TIMEOUT=_f("JUDGE0_HTTP_TIMEOUT", 15), JUDGE0_MAX_RETRIES=_i("JUDGE0_MAX_RETRIES", 3),
        CPU_TIME_LIMIT=_f("CPU_TIME_LIMIT", 2), MEMORY_LIMIT_KB=_i("MEMORY_LIMIT_KB", 128000),
        # --- limits ---
        MAX_CODE_BYTES=_i("MAX_CODE_BYTES", 50000), MAX_STDIN_BYTES=_i("MAX_STDIN_BYTES", 10000),
        RATE_RUN_PER_MIN=_i("RATE_RUN_PER_MIN", 20), RATE_SUBMIT_PER_MIN=_i("RATE_SUBMIT_PER_MIN", 6),
        LOGIN_MAX_FAILS=_i("LOGIN_MAX_FAILS", 5), LOGIN_WINDOW_SEC=_i("LOGIN_WINDOW_SEC", 300),
        STALE_EVAL_MINUTES=_i("STALE_EVAL_MINUTES", 10),
        # --- review aids ---
        SIM_THRESHOLD=_f("SIM_THRESHOLD", 0.85), SIM_MIN_TOKENS=_i("SIM_MIN_TOKENS", 25),
        PASTE_DETERRENT=_b("PASTE_DETERRENT", True),
        EVAL_SYNC=_b("EVAL_SYNC", False), SEED_DEMO=_b("SEED_DEMO", True),
    )
    if overrides: c.update(overrides)
    return c
