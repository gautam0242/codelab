"""Check your Judge0 configuration:  python check_executor.py
Verifies the language id exists, reports the compiler name, and runs 3 small programs through the REAL service."""
import sys
from config import load_config
from executor import Judge0Executor, ExecutorError

def main():
    cfg = load_config(); ex = Judge0Executor(cfg)
    print("Service:", ex.describe())
    if not ex.configured: sys.exit("JUDGE0_URL is empty. Fill in .env first (see README).")
    ok = True
    try:
        v = ex.verify(); print(f"[ok] language id {v['language_id']} = {v['language']}")
        print("     C++ languages on this service:", *v["cpp_languages"], sep="\n       ")
        r = ex.run('#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}', "20 22")
        good = r["kind"] == "ok" and r["stdout"].strip() == "42"; ok &= good
        print(f"[{'ok' if good else 'FAIL'}] stdin/stdout program -> {r['stdout']!r} ({r['status']}, {r['time']}s)")
        r = ex.run("int main(){ int x = ; }"); good = r["kind"] == "compile_error"; ok &= good
        print(f"[{'ok' if good else 'FAIL'}] compile error detected -> {r['status']}")
        r = ex.run("int main(){ volatile unsigned long long x=0; while(true) x++; }", cpu_time=1); good = r["kind"] == "timeout"; ok &= good
        print(f"[{'ok' if good else 'FAIL'}] time limit enforced -> {r['status']}")
    except ExecutorError as e:
        ok = False; print(f"[FAIL] {e.kind}: {e.message}")
    sys.exit(0 if ok else 1)
if __name__ == "__main__": main()
