"""
Entry point for Raina AI — with auto crash-restart.

On a VPS: run with `python run.py` under systemd or pm2.
The inner loop catches crashes and restarts automatically
with exponential back-off (5s → 10s → 20s … max 60s).
"""
import os
import sys
import time
import subprocess
from dotenv import load_dotenv

load_dotenv()


def _cmd() -> list[str]:
    host = os.getenv("HOST", "0.0.0.0")
    port = os.getenv("PORT", "8000")
    return [
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", host,
        "--port", port,
    ]


if __name__ == "__main__":
    backoff = 5
    cwd = os.path.dirname(os.path.abspath(__file__))
    while True:
        print(f"[Raina AI] Starting server...", flush=True)
        result = subprocess.run(_cmd(), cwd=cwd)
        if result.returncode == 0:
            print("[Raina AI] Clean shutdown.", flush=True)
            break
        print(
            f"[Raina AI] Process exited with code {result.returncode}. "
            f"Restarting in {backoff}s...",
            flush=True,
        )
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)
