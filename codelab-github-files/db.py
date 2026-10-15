"""Tiny database layer: SQLite for local use, PostgreSQL (psycopg2) for hosting.
SQL is written once with '?' placeholders; they are translated for PostgreSQL."""
import os, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
try:
    import psycopg2, psycopg2.extras
except ImportError:  # PostgreSQL support is optional
    psycopg2 = None
import schema

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

class Session:
    def __init__(self, conn, pg):
        self.c, self.pg = conn, pg
    def _cur(self):
        return self.c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if self.pg else self.c.cursor()
    def _sql(self, sql):
        return sql.replace("?", "%s") if self.pg else sql
    def q(self, sql, args=()):
        cur = self._cur(); cur.execute(self._sql(sql), tuple(args))
        return [dict(r) for r in cur.fetchall()]
    def one(self, sql, args=()):
        rows = self.q(sql, args); return rows[0] if rows else None
    def exec(self, sql, args=()):
        cur = self._cur(); cur.execute(self._sql(sql), tuple(args)); return cur.rowcount
    def insert(self, table, **vals):
        cols = ",".join(vals); ph = ",".join("?" * len(vals))
        sql = f"INSERT INTO {table}({cols}) VALUES({ph})"
        cur = self._cur()
        if self.pg:
            cur.execute(self._sql(sql + " RETURNING id"), tuple(vals.values())); return cur.fetchone()["id"]
        cur.execute(sql, tuple(vals.values())); return cur.lastrowid
    def commit(self): self.c.commit()
    def rollback(self): self.c.rollback()

class DB:
    def __init__(self, url):
        self.url = url
        self.pg = url.startswith("postgresql://")
        if self.pg:
            if psycopg2 is None: raise RuntimeError("DATABASE_URL points to PostgreSQL but psycopg2 is not installed")
            self.IntegrityError = (sqlite3.IntegrityError, psycopg2.IntegrityError)
        else:
            if not url.startswith("sqlite:///"): raise RuntimeError("DATABASE_URL must start with sqlite:/// or postgresql://")
            self.path = url[len("sqlite:///"):]
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            self.IntegrityError = (sqlite3.IntegrityError,)
    def _connect(self):
        if self.pg: return psycopg2.connect(self.url)
        c = sqlite3.connect(self.path, timeout=15); c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON"); return c
    @contextmanager
    def session(self):
        conn = self._connect(); s = Session(conn, self.pg)
        try:
            yield s; conn.commit()
        except BaseException:
            conn.rollback(); raise
        finally:
            conn.close()
    def create_all(self):
        with self.session() as s:
            for st in schema.STATEMENTS:
                if self.pg: st = st.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                s.exec(st)
        if not self.pg:
            c = self._connect(); c.execute("PRAGMA journal_mode=WAL"); c.close()
    def drop_all(self):
        with self.session() as s:
            for t in ("events", "test_results", "submissions", "tests", "assignments", "users", "meta"):
                s.exec(f"DROP TABLE IF EXISTS {t}" + (" CASCADE" if self.pg else ""))
