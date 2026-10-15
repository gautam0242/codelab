"""Database schema (SQLite and PostgreSQL compatible)."""
STATEMENTS = [
"""CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('student','teacher')), pw_hash TEXT NOT NULL, created_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS assignments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, unit TEXT NOT NULL, description TEXT NOT NULL,
  input_format TEXT NOT NULL DEFAULT '', output_format TEXT NOT NULL DEFAULT '', constraints_text TEXT NOT NULL DEFAULT '',
  deadline TEXT NOT NULL DEFAULT '', starter TEXT NOT NULL, compare_mode TEXT NOT NULL DEFAULT 'lenient',
  time_limit REAL NOT NULL DEFAULT 2, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS tests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
  position INTEGER NOT NULL DEFAULT 0, input TEXT NOT NULL, expected TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0)""",
"""CREATE TABLE IF NOT EXISTS submissions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
  assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE, code TEXT NOT NULL,
  attempt INTEGER NOT NULL, status TEXT NOT NULL, passed_count INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
  compile_output TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', client_token TEXT NOT NULL,
  created_at TEXT NOT NULL, evaluated_at TEXT)""",
"""CREATE TABLE IF NOT EXISTS test_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT, submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  test_id INTEGER REFERENCES tests(id) ON DELETE SET NULL, position INTEGER NOT NULL, hidden INTEGER NOT NULL,
  status TEXT NOT NULL, exec_status TEXT NOT NULL DEFAULT '', input_snapshot TEXT NOT NULL DEFAULT '',
  expected_snapshot TEXT NOT NULL DEFAULT '', actual_output TEXT NOT NULL DEFAULT '', stderr TEXT NOT NULL DEFAULT '',
  time REAL, memory INTEGER)""",
"""CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, actor_id INTEGER,
  assignment_id INTEGER REFERENCES assignments(id) ON DELETE CASCADE,
  submission_id INTEGER REFERENCES submissions(id) ON DELETE CASCADE,
  type TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""",
"CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
"CREATE UNIQUE INDEX IF NOT EXISTS ux_sub_token ON submissions(user_id, client_token)",
"CREATE UNIQUE INDEX IF NOT EXISTS ux_sub_attempt ON submissions(user_id, assignment_id, attempt)",
# at most ONE submission per student+assignment may be waiting/being evaluated (atomic duplicate-submit guard)
"""CREATE UNIQUE INDEX IF NOT EXISTS ux_sub_active ON submissions(user_id, assignment_id)
   WHERE status IN ('submitted','evaluation_pending','evaluating')""",
"CREATE INDEX IF NOT EXISTS ix_sub_assign ON submissions(assignment_id, user_id)",
"CREATE INDEX IF NOT EXISTS ix_sub_status ON submissions(status)",
"CREATE INDEX IF NOT EXISTS ix_tests_assign ON tests(assignment_id)",
"CREATE INDEX IF NOT EXISTS ix_results_sub ON test_results(submission_id)",
"CREATE INDEX IF NOT EXISTS ix_events_user ON events(user_id, created_at)",
]
