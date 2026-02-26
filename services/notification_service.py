import json
import time
import threading
from datetime import datetime, date, timedelta, timezone

# Time to send the daily morning briefing (24h format, local time)
MORNING_BRIEFING_TIME = "08:00"


# Minimum away duration before a welcome-back notification is sent.
# Prevents spurious notifications when RSSI bounces through a full
# away→home cycle without the user actually leaving.
MIN_AWAY_DURATION = 30 * 60  # 30 minutes


class NotificationService:
    _poor_air_alerted: bool = False
    _last_briefing_date: date | None = None
    _rain_notified: bool = False                 # reset each away session
    _notified_meetings: set[str] = set()         # event start keys notified this away session
    _left_at: float | None = None                # timestamp of last confirmed departure

    @classmethod
    def start(cls):
        from smart_home.home_context_service import HomeContextService
        HomeContextService.register_on_arrive(cls._on_arrive)
        HomeContextService.register_on_leave(cls._on_leave)

        threading.Thread(target=cls._run_air_quality_monitor, daemon=True).start()
        threading.Thread(target=cls._run_scheduler, daemon=True).start()

        print("[Notification] Service started.")

    # ------------------------------------------------------------------ #
    #  Presence callbacks                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def _on_arrive(cls):
        now = datetime.now()

        # Skip notification if the user was never away long enough to count as a real departure
        if cls._left_at is None or (now.timestamp() - cls._left_at) < MIN_AWAY_DURATION:
            return

        from agents.memory_service import MemoryService
        threading.Thread(
            target=MemoryService.observe,
            args=(f"User arrived home at {now.strftime('%H:%M')} ({now.strftime('%A')})",),
            daemon=True,
        ).start()

        from agents.persona_agent import PersonaAgent
        from services.telegram_service import TelegramService
        text = PersonaAgent.generate_reactive_line("the user just arrived home, you can comment about the situation as if you returned together, "
            "or as if the user returned and you welcome him back")
        TelegramService.send_message(text, photo=TelegramService.get_image_for_text(text))

    @classmethod
    def _on_leave(cls):
        # Record departure time and reset away-session notification guards
        cls._left_at = time.time()
        cls._rain_notified = False
        cls._notified_meetings.clear()

        from smart_home.smart_home_service import get_device_status, change_device_status
        try:
            device = get_device_status('led')
            lights_on = device.activated
        except Exception:
            lights_on = False

        if not lights_on:
            return  # nothing to do

        from agents.memory_service import MemoryService
        threading.Thread(
            target=MemoryService.observe,
            args=("User left home with the lights still on",),
            daemon=True,
        ).start()

        from agents.persona_agent import PersonaAgent
        from services.telegram_service import TelegramService
        text = PersonaAgent.generate_reactive_line("the user just left home and the lights are still on")

        import telebot
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(
            telebot.types.InlineKeyboardButton("Yes~", callback_data="confirm:lights_off"),
            telebot.types.InlineKeyboardButton("Never mind", callback_data="dismiss"),
        )
        TelegramService.register_pending_action("confirm:lights_off", lambda: change_device_status('led', False))
        TelegramService.send_message(text, reply_markup=markup, photo=TelegramService.get_image_for_text(text))

    # ------------------------------------------------------------------ #
    #  Air quality monitor                                                 #
    # ------------------------------------------------------------------ #

    @classmethod
    def _run_air_quality_monitor(cls):
        """Background thread: checks air quality every 60s and sends an alert when it spikes."""
        time.sleep(30)  # give server time to settle on startup
        while True:
            try:
                from smart_home.home_context_service import HomeContextService
                from agents.persona_agent import PersonaAgent
                from services.telegram_service import TelegramService
                poor = HomeContextService.has_poor_air()
                voc = HomeContextService._voc
                if poor and not cls._poor_air_alerted:
                    situation = f"indoor air quality is poor (VOC index {int(voc) if voc else '?'}), air smells stale and stuffy"
                    text = PersonaAgent.generate_reactive_line(situation)
                    TelegramService.send_message(text, photo=TelegramService.get_image_for_text(text))
                    cls._poor_air_alerted = True
                elif not poor and cls._poor_air_alerted:
                    cls._poor_air_alerted = False  # reset so next spike triggers again
            except Exception as e:
                print(f"[Notification] Air quality monitor error: {e}")
            time.sleep(60)

    # ------------------------------------------------------------------ #
    #  Scheduler (morning briefing, rain warning, meeting reminder)        #
    # ------------------------------------------------------------------ #

    @classmethod
    def _run_scheduler(cls):
        """Single daemon thread for all timed jobs. Polls every 60s."""
        time.sleep(10)  # brief startup delay
        while True:
            try:
                cls._check_morning_briefing()
                cls._check_rain_warning()
                cls._check_meeting_reminder()
            except Exception as e:
                print(f"[Notification] Scheduler error: {e}")
            time.sleep(60)

    @classmethod
    def _check_morning_briefing(cls):
        now = datetime.now()
        today = now.date()
        if now.strftime("%H:%M") == MORNING_BRIEFING_TIME and cls._last_briefing_date != today:
            cls._last_briefing_date = today  # set before sending to prevent double-fire
            threading.Thread(target=cls._send_morning_briefing, daemon=True).start()

    @classmethod
    def _send_morning_briefing(cls):
        try:
            from agents.weather_agent_service import WeatherAgentService
            from agents.calendar_agent_service import CalendarAgentService
            from agents.persona_agent import PersonaAgent
            from models import Task
            from services.telegram_service import TelegramService

            weather = WeatherAgentService.get_weather("today")
            events = CalendarAgentService.get_calendar_events("today")
            pending_tasks = [t.task_name for t in Task.select().where(Task.completed == False)]

            context_parts = [f"Weather: {weather}", f"Calendar: {events}"]
            context_parts.append(f"Pending tasks: {', '.join(pending_tasks)}" if pending_tasks else "Pending tasks: none")

            context = ". ".join(context_parts)
            text = PersonaAgent.generate_morning_briefing(context)
            TelegramService.send_message(text, photo=TelegramService.get_image_for_text(text))
        except Exception as e:
            print(f"[Notification] Morning briefing failed: {e}")

    @classmethod
    def _check_rain_warning(cls):
        from smart_home.home_context_service import HomeContextService
        if HomeContextService.is_home() or cls._rain_notified:
            return
        try:
            from services.weather_service import get_cached_or_fetch, get_default_location
            from agents.persona_agent import PersonaAgent
            from services.telegram_service import TelegramService

            location = get_default_location()
            city_data = get_cached_or_fetch([location]).get(location)
            if not city_data:
                return
            precip = json.loads(city_data["hourly_precipitation"])
            first_time = datetime.fromisoformat(city_data["first_time"])
            now = datetime.now()
            idx = max(0, min(int((now - first_time).total_seconds() // 3600), len(precip) - 1))

            # Only warn if it's not already raining but rain starts within 2 hours
            if precip[idx] > 0.1:
                return
            rain_idx = next((idx + i for i in range(1, 3) if idx + i < len(precip) and precip[idx + i] > 0.1), None)
            if rain_idx is None:
                return

            cls._rain_notified = True
            rain_time = (first_time + timedelta(hours=rain_idx)).strftime('%H:%M')
            text = PersonaAgent.generate_reactive_line(f"the user is away from home and rain is starting around {rain_time}")
            TelegramService.send_message(text, photo=TelegramService.get_image_for_text(text))
        except Exception as e:
            print(f"[Notification] Rain warning check failed: {e}")

    @classmethod
    def _check_meeting_reminder(cls):
        from smart_home.home_context_service import HomeContextService
        if HomeContextService.is_home():
            return
        try:
            from services.google_calendar import get_all_events
            from services.calendar_utils import parse_dt
            from agents.persona_agent import PersonaAgent
            from services.telegram_service import TelegramService

            now = datetime.now(timezone.utc)
            soon = now + timedelta(minutes=30)

            for event in get_all_events():
                start_str = event.get('start', {}).get('dateTime')
                if not start_str:
                    continue
                start = parse_dt(start_str)
                if not (now < start <= soon):
                    continue
                if start_str in cls._notified_meetings:
                    continue
                cls._notified_meetings.add(start_str)
                name = event.get('summary') or 'a meeting'
                minutes = max(1, int((start - now).total_seconds() // 60))
                text = PersonaAgent.generate_reactive_line(
                    f"the user is away from home and '{name}' starts in {minutes} minutes"
                )
                TelegramService.send_message(text, photo=TelegramService.get_image_for_text(text))
        except Exception as e:
            print(f"[Notification] Meeting reminder check failed: {e}")
