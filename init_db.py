"""Create tables and (optionally) demo data.  Usage:
  python init_db.py            create tables, seed demo data only if the database has no users
  python init_db.py --reset --yes   DELETE ALL DATA and recreate demo data (refused in production unless --force)"""
import sys
from werkzeug.security import generate_password_hash as h
from config import load_config
from db import DB, now

STARTER = "#include <iostream>\nusing namespace std;\n\nint main() {\n    // Write your code here\n    return 0;\n}\n"
DEMO_USERS = [("student01", "Demo Student 01", "student", "Student@123"), ("teacher01", "Demo Teacher 01", "teacher", "Teacher@123"),
              ("student02", "Demo Student 02", "student", "Student@456")]
ASSIGNMENTS = [
 ("Sum of Two Numbers", "Unit 1: Basic I/O", "Read two integers and print their sum.", "Two integers separated by a space.", "A single integer: the sum.", "-10^9 <= a, b <= 10^9",
  [("3 4\n", "7", 0), ("10 20\n", "30", 0), ("-5 5\n", "0", 1), ("1000000000 1000000000\n", "2000000000", 1)]),
 ("Even or Odd", "Unit 2: Conditions", "Read an integer and print Even or Odd.", "One integer.", "The word Even or Odd.", "-10^9 <= n <= 10^9",
  [("4\n", "Even", 0), ("7\n", "Odd", 0), ("0\n", "Even", 1), ("-3\n", "Odd", 1)]),
 ("Factorial", "Unit 3: Loops", "Read n and print n! using a loop.", "One integer n.", "n! as an integer.", "0 <= n <= 12",
  [("5\n", "120", 0), ("0\n", "1", 0), ("12\n", "479001600", 1), ("1\n", "1", 1)]),
 ("Maximum in Array", "Unit 4: Arrays", "Read n, then n integers. Print the largest.", "First line n, second line n integers.", "The maximum value.", "1 <= n <= 100",
  [("5\n3 9 2 7 1\n", "9", 0), ("3\n-5 -2 -9\n", "-2", 1), ("1\n42\n", "42", 1)]),
]

def seed(db):
    with db.session() as s:
        if s.one("SELECT 1 AS x FROM users"): return False
        for u, n, r, p in DEMO_USERS: s.insert("users", username=u, name=n, role=r, pw_hash=h(p), created_at=now())
        for t, unit, d, i, o, c, tests in ASSIGNMENTS:
            aid = s.insert("assignments", title=t, unit=unit, description=d, input_format=i, output_format=o, constraints_text=c,
                           deadline="2026-12-31", starter=STARTER, created_at=now())
            for pos, (inp, exp, hid) in enumerate(tests, 1): s.insert("tests", assignment_id=aid, position=pos, input=inp, expected=exp, hidden=hid)
    return True

if __name__ == "__main__":
    cfg = load_config(); db = DB(cfg["DATABASE_URL"])
    if "--reset" in sys.argv:
        if "--yes" not in sys.argv: sys.exit("--reset deletes ALL data. Add --yes to confirm.")
        if cfg["APP_ENV"] == "production" and "--force" not in sys.argv: sys.exit("Refusing to reset a production database without --force.")
        db.drop_all()
    db.create_all()
    print("Demo data created." if cfg["SEED_DEMO"] and seed(db) else "Tables ready (existing data kept).")
