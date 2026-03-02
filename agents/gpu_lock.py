"""Cross-process GPU lock shared by image_gen_service.py (Flask) and hq_gen_worker.py.

Uses portalocker (flock/LockFileEx) — auto-released when the holding process
dies for any reason, since the OS closes all fds on exit.
"""
import os
import signal
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import portalocker

GPU_LOCK_PATH   = Path("env/gpu.lock")
WORKER_PID_PATH = Path("env/hq_worker.pid")

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
    Raises RuntimeError after _GPU_LOCK_TIMEOUT seconds.
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

                # Fallback: kill the worker if it still holds the lock past the threshold.
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
