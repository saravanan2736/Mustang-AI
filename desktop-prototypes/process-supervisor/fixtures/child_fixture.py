"""
Stand-in for the Mustang AI Python backend in the supervision prototype.

Optionally spawns a long-lived grandchild (stand-in for llama-server.exe), writes to
stdout and stderr, and exits cleanly when it reads "shutdown" (or EOF) on stdin.
Standard library only.
"""

import argparse
import os
import subprocess
import sys
import threading
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", type=int, default=0)
    ap.add_argument("--grandchild", action="store_true")
    ap.add_argument("--crash-after", type=float, default=0.0)
    ap.add_argument("--ignore-shutdown", action="store_true")
    ap.add_argument("--echo-secret-env", default="")
    args = ap.parse_args()

    print(f"FIXTURE_PID={os.getpid()}", flush=True)

    grandchild = None
    if args.grandchild:
        grandchild = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        print(f"GRANDCHILD_PID={grandchild.pid}", flush=True)

    for i in range(args.lines):
        print(f"stdout line {i}", flush=True)
        print(f"stderr line {i}", file=sys.stderr, flush=True)

    if args.echo_secret_env:
        print(f"secret value is {os.environ.get(args.echo_secret_env, '')}", flush=True)

    print("READY", flush=True)

    if args.crash_after:
        def crash():
            time.sleep(args.crash_after)
            print("simulated crash", file=sys.stderr, flush=True)
            os._exit(3)
        threading.Thread(target=crash, daemon=True).start()

    for line in sys.stdin:
        if line.strip() == "shutdown":
            break
    # stdin EOF or "shutdown"
    if args.ignore_shutdown:
        print("ignoring shutdown request", flush=True)
        while True:
            time.sleep(1)

    if grandchild is not None:
        grandchild.terminate()
        grandchild.wait(timeout=5)
    print("graceful shutdown complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
