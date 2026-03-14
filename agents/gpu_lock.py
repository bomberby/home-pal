"""GPU coordination layer shared by image_gen_service.py (Flask) and hq_gen_worker.py.

Owns all GPU-access coordination:
- gpu_lock()             — cross-process flock (portalocker); auto-released on process death.
- claim_gpu()            — two-stage preemption: priority marker + optional worker SIGTERM.
- write/clear_priority() — priority queue markers that tell the worker to yield.
- signal_worker()        — SIGTERM the HQ worker and retire its PID file.
- cleanup_stale_*()      — startup cleanup of coordination files from a previous run.

All coordination file paths (GPU_LOCK_PATH, WORKER_PID_PATH, WORKER_HEARTBEAT_PATH,
PRIORITY_QUEUE_DIR) are defined here as the single source of truth.
"""
import json
import os
import signal
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import portalocker

GPU_LOCK_PATH         = Path("env/gpu.lock")
WORKER_PID_PATH       = Path("env/hq_worker.pid")
WORKER_HEARTBEAT_PATH = Path("env/hq_worker.heartbeat")
PRIORITY_QUEUE_DIR    = Path("env/priority_queue")

_GPU_LOCK_TIMEOUT = 600.0       # seconds before gpu_lock() raises instead of spinning
_GPU_LOCK_LOG_INTERVAL = 30.0   # how often to repeat the "still waiting" warning
_WORKER_KILL_AFTER  = 5.0       # seconds before force-killing the HQ worker if it still holds the lock


def _gpu_log(msg: str) -> None:
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except OSError:
        pass


@contextmanager
def gpu_lock():
    """Acquire the GPU lock, blocking until it is available.

    The lock file is never unlinked while held — unlinking an open file gives
    concurrent waiters a stale inode and silently breaks mutual exclusion.
    """
    import traceback as _tb

    GPU_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    _started_at = time.monotonic()
    _next_log_at = _started_at + _GPU_LOCK_LOG_INTERVAL
    _contention_logged = False
    _kill_attempted = False

    # 'a+b': create-if-absent, never truncate. Fd must stay open — closing releases the lock.
    f = open(GPU_LOCK_PATH, "a+b")
    try:
        while True:
            try:
                portalocker.lock(f, portalocker.LOCK_EX | portalocker.LOCK_NB)
                f.seek(0)
                f.truncate()
                f.write(str(os.getpid()).encode())
                f.flush()
                break
            except portalocker.LockException:
                now = time.monotonic()
                elapsed = now - _started_at

                # Read holder PID from hq_worker.pid (avoids touching the locked file).
                holder_pid: int | None = None
                try:
                    holder_pid = int(WORKER_PID_PATH.read_text().strip())
                except (ValueError, OSError):
                    pass

                if elapsed >= _GPU_LOCK_TIMEOUT:
                    caller = "".join(_tb.format_stack()[:-1])
                    _gpu_log(
                        f"[ImageGen][CRITICAL] gpu_lock timed out after {elapsed:.0f}s.\n"
                        f"  Holder PID: {holder_pid}\n"
                        f"  My PID:     {os.getpid()}\n"
                        f"  Lock file:  {GPU_LOCK_PATH.resolve()}\n"
                        f"  Caller stack:\n{caller}"
                    )
                    raise RuntimeError(
                        f"gpu_lock: timed out after {elapsed:.0f}s waiting for PID {holder_pid}"
                    )

                if not _contention_logged:
                    _gpu_log(
                        f"[ImageGen] gpu_lock: waiting for PID {holder_pid} to release lock "
                        f"(my PID={os.getpid()})."
                    )
                    _contention_logged = True
                elif now >= _next_log_at:
                    _gpu_log(
                        f"[ImageGen][WARN] gpu_lock: still waiting for PID {holder_pid} "
                        f"({elapsed:.0f}s elapsed, my PID={os.getpid()})."
                    )
                    _next_log_at = now + _GPU_LOCK_LOG_INTERVAL

                if (
                    not _kill_attempted
                    and elapsed >= _WORKER_KILL_AFTER
                    and holder_pid is not None
                    and holder_pid != os.getpid()
                ):
                    _gpu_log(
                        f"[ImageGen] gpu_lock: worker (PID {holder_pid}) still holds lock "
                        f"after {elapsed:.0f}s — sending SIGTERM (my PID={os.getpid()})."
                    )
                    try:
                        os.kill(holder_pid, signal.SIGTERM)
                    except (ProcessLookupError, OSError):
                        pass
                    WORKER_PID_PATH.unlink(missing_ok=True)
                    _kill_attempted = True

                time.sleep(0.5)

        try:
            yield
        finally:
            portalocker.unlock(f)
            # Clear PID — do NOT unlink (see docstring).
            try:
                f.seek(0)
                f.truncate()
            except OSError:
                pass
    finally:
        try:
            f.close()
        except OSError:
            pass


def cleanup_stale_lock() -> None:
    """Remove coordination files left by a previous server run.

    Called once at startup — a lock file or stale heartbeat from a dead process
    must not block the new run.
    """
    if GPU_LOCK_PATH.exists():
        _gpu_log("[ImageGen][WARN] Stale gpu.lock found at startup — removing.")
        GPU_LOCK_PATH.unlink(missing_ok=True)
    WORKER_HEARTBEAT_PATH.unlink(missing_ok=True)


def cleanup_stale_priority() -> None:
    """Remove priority marker files left by a previous crash.

    Priority files are written by generate() and claim_gpu() as in-flight
    signals to the HQ worker.  A crashed Flask process leaves them behind,
    causing the worker to loop in the priority guard indefinitely.
    """
    if not PRIORITY_QUEUE_DIR.exists():
        return
    stale = list(PRIORITY_QUEUE_DIR.glob("*.json"))
    if stale:
        for f in stale:
            f.unlink(missing_ok=True)
        _gpu_log(f"[ImageGen] Cleared {len(stale)} stale priority file(s) at startup.")


def write_priority(key: str) -> None:
    """Write a priority marker so the HQ worker yields before calling gpu_lock()."""
    PRIORITY_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    pf = PRIORITY_QUEUE_DIR / f"{key}.json"
    if not pf.exists():
        pf.write_text(json.dumps({"state": key}))


def clear_priority(key: str, on_empty=None) -> None:
    """Remove the priority marker for *key*.

    If no markers remain and *on_empty* is provided, call it (typically used
    to restart the HQ worker after a preemption).
    """
    (PRIORITY_QUEUE_DIR / f"{key}.json").unlink(missing_ok=True)
    remaining = list(PRIORITY_QUEUE_DIR.glob("*.json")) if PRIORITY_QUEUE_DIR.exists() else []
    if not remaining and on_empty is not None:
        on_empty()


def signal_worker() -> None:
    """Send SIGTERM to the HQ worker and delete its PID file."""
    if not WORKER_PID_PATH.exists():
        return
    try:
        pid = int(WORKER_PID_PATH.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        _gpu_log(f"[ImageGen] Sent SIGTERM to worker (PID {pid}) — priority request.")
    except (ProcessLookupError, ValueError, OSError):
        pass
    finally:
        WORKER_PID_PATH.unlink(missing_ok=True)


@contextmanager
def claim_gpu(key: str = "_claim", skip_if=None, on_worker_killed=None):
    """Context manager: acquire the GPU for a high-priority workload.

    Two-stage preemption:

    1. Write a priority marker so the worker yields voluntarily right before
       calling gpu_lock().  This closes the TOCTOU window where the worker is
       computing embeddings (CPU-only, no lock held yet) without killing it.

    2. If the worker is currently holding the lock (actively doing GPU
       inference) send SIGTERM and restart it afterward.
    """
    if skip_if is not None and skip_if():
        yield
        return

    worker_killed = False
    write_priority(key)
    try:
        try:
            lock_pid_int = int(GPU_LOCK_PATH.read_text().strip())
            if lock_pid_int != os.getpid():
                try:
                    os.kill(lock_pid_int, signal.SIGTERM)
                    worker_killed = True
                except (ProcessLookupError, OSError):
                    pass
                WORKER_PID_PATH.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

        with gpu_lock():
            yield
    finally:
        clear_priority(key, on_empty=on_worker_killed)
        if worker_killed and on_worker_killed:
            on_worker_killed()
