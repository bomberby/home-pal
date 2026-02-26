"""DB-persisted time-based reminders.

Reminders are stored as Task rows with task_name prefixed '[reminder] '.
The scheduler polls every 30 seconds, fires a persona-styled Telegram
notification for each due reminder, then marks it completed.

Survives server restarts — reminders set before a restart will still fire.
"""
import threading
import time

_started = False
_lock = threading.Lock()


def start():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_run, daemon=True).start()
    print("[Reminder] Scheduler started.")


def _run():
    while True:
        try:
            from models import database
            database.connect(reuse_if_open=True)
            _check_due()
        except Exception as e:
            print(f"[Reminder] Check failed: {e}")
        finally:
            try:
                from models import database
                if not database.is_closed():
                    database.close()
            except Exception:
                pass
        time.sleep(30)


def _check_due():
    from models import Task
    from services.local_time import get_local_now
    now = get_local_now()
    due = (
        Task.select()
        .where(
            Task.task_name.startswith('[reminder]'),
            Task.due_date <= now,
            Task.completed == False,
        )
    )
    for task in due:
        label = task.task_name.removeprefix('[reminder]').strip()
        print(f"[Reminder] Firing: '{label}'")
        try:
            from services.telegram_service import TelegramService
            from agents.persona_agent import PersonaAgent
            situation = f"a scheduled reminder just triggered: '{label}'"
            message = PersonaAgent.generate_reactive_line(situation)
            TelegramService.send_message(message, photo=TelegramService.get_image_for_text(message))
        except Exception as e:
            print(f"[Reminder] Notification failed: {e}")
        task.completed = True
        task.save()
