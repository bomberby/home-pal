"""HQ worker subprocess lifecycle: spawn, boot handshake, heartbeat check, atexit cleanup."""
import atexit
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from agents.image.gpu_lock import GPU_LOCK_PATH, WORKER_HEARTBEAT_PATH, WORKER_PID_PATH

WORKER_BOOT_PATH      = Path("env/hq_worker.booting")
_WORKER_HEARTBEAT_TTL = 90.0  # seconds — 3× the worker's 30s heartbeat write interval

_start_worker_lock  = threading.Lock()
_atexit_registered  = False


def is_worker_healthy() -> bool:
    """Return True if the HQ worker has written a fresh heartbeat recently."""
    if not WORKER_HEARTBEAT_PATH.exists():
        return False
    try:
        age = time.time() - float(WORKER_HEARTBEAT_PATH.read_text().strip())
        return age < _WORKER_HEARTBEAT_TTL
    except (ValueError, OSError):
        return False


def start_hq_worker() -> None:
    """Start hq_gen_worker.py as a detached subprocess. Guarded by a heartbeat file.

    The lock prevents two rapid callers from both concluding the worker is dead
    and each spawning a new one.
    """
    global _atexit_registered

    with _start_worker_lock:
        pid_file = WORKER_PID_PATH
        pid_file.parent.mkdir(parents=True, exist_ok=True)

        # Healthy heartbeat → worker is alive, nothing to do
        if is_worker_healthy():
            return

        # No heartbeat yet — check whether we're still in the boot window
        if WORKER_BOOT_PATH.exists():
            try:
                boot_age = time.time() - WORKER_BOOT_PATH.stat().st_mtime
            except OSError:
                boot_age = 0
            if boot_age < 120:
                return  # still booting, give it time
            print(f"[ImageGen] Worker stuck in boot ({boot_age:.0f}s) — respawning.")
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, ValueError, OSError):
                    pass
            pid_file.unlink(missing_ok=True)
            WORKER_BOOT_PATH.unlink(missing_ok=True)

        WORKER_BOOT_PATH.write_text(str(time.time()))
        proc = subprocess.Popen([sys.executable, '-m', 'agents.image.hq_gen_worker'])
        pid_file.write_text(str(proc.pid))
        print(f"[ImageGen] HQ worker spawned (proc.pid={proc.pid}), waiting for boot confirmation.")

        # Register the atexit handler only ONCE for the lifetime of this Flask process.
        # We must NOT capture `proc` in the closure — doing so creates a new closure per
        # worker restart, accumulating stale atexit handlers that send SIGTERM to recycled
        # PIDs and delete gpu.lock while the *current* worker still holds it.
        if not _atexit_registered:
            _pid_file_path = pid_file  # stable Path reference; content changes with restarts

            def _shutdown_worker():
                # Read the *current* PID from the file — not a stale closure value.
                try:
                    current_pid = int(_pid_file_path.read_text().strip())
                    os.kill(current_pid, signal.SIGTERM)
                    print(f"[ImageGen] HQ worker (PID {current_pid}) terminated.")
                except (ProcessLookupError, OSError, ValueError):
                    pass
                _pid_file_path.unlink(missing_ok=True)
                WORKER_BOOT_PATH.unlink(missing_ok=True)
                WORKER_HEARTBEAT_PATH.unlink(missing_ok=True)
                GPU_LOCK_PATH.unlink(missing_ok=True)

            atexit.register(_shutdown_worker)
            _atexit_registered = True
