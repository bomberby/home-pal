import json
import os
import re
import time
import threading
from collections import deque

SECRETS_PATH = os.path.join('env', 'secrets', 'telegram.json')

# When True: wait for SD to generate the mood-matched image before sending.
# Guarantees the correct mood image every time, but adds generation delay (~30s on GPU).
# When False: send immediately with the current cached image; generate mood image in background.
WAIT_FOR_MOOD_IMAGE = True


def _load_secrets() -> tuple[str, str]:
    try:
        with open(SECRETS_PATH) as f:
            data = json.load(f)
        return data.get('bot_token', ''), data.get('chat_id', '')
    except FileNotFoundError:
        return '', ''
    except Exception as e:
        print(f"[Telegram] Could not read {SECRETS_PATH}: {e}")
        return '', ''


class TelegramService:
    _bot = None
    _chat_id: str | None = None
    _pending_actions: dict[str, callable] = {}
    _history: deque = deque(maxlen=3)  # last 3 (user, persona) exchanges

    @classmethod
    def _format_history(cls) -> str | None:
        if not cls._history:
            return None
        lines = []
        for user_msg, persona_msg in cls._history:
            lines.append(f"User: {user_msg}")
            lines.append(f"You: {persona_msg}")
        return "\n".join(lines)  # callback_data → callable

    @classmethod
    def start(cls):
        bot_token, chat_id = _load_secrets()
        if not bot_token or not chat_id:
            print("[Telegram] Secrets not found — bot disabled. Create env/secrets/telegram.json.")
            return
        try:
            import telebot  # noqa: F401
        except ImportError:
            print("[Telegram] pyTelegramBotAPI not installed — run: pip install pyTelegramBotAPI")
            return

        import telebot
        cls._chat_id = str(chat_id)
        cls._bot = telebot.TeleBot(bot_token, parse_mode=None)

        # Register message and callback handlers
        @cls._bot.message_handler(func=lambda m: True)
        def on_text(message):
            # Only handle messages from the configured chat
            if str(message.chat.id) != cls._chat_id:
                return
            cls._handle_text(message)

        @cls._bot.callback_query_handler(func=lambda c: True)
        def on_callback(call):
            cls._handle_callback(call)

        # Start notification state machine
        from services.notification_service import NotificationService
        NotificationService.start()

        # Start polling thread
        threading.Thread(target=cls._run_polling, daemon=True).start()

        print("[Telegram] Bot started.")

    @classmethod
    def is_available(cls) -> bool:
        return cls._bot is not None and cls._chat_id is not None

    @classmethod
    def register_pending_action(cls, callback_data: str, action: callable):
        cls._pending_actions[callback_data] = action

    @classmethod
    def send_message(cls, text: str, reply_markup=None, photo: str | None = None):
        if not cls.is_available():
            return
        try:
            if photo and os.path.exists(photo):
                from PIL import Image
                from io import BytesIO
                img = Image.open(photo)
                img = img.resize((int(img.width / 2), int(img.height / 2)), Image.LANCZOS)
                buf = BytesIO()
                img.save(buf, format='PNG')
                buf.seek(0)
                cls._bot.send_photo(cls._chat_id, buf, caption=text, reply_markup=reply_markup)
            else:
                cls._bot.send_message(cls._chat_id, text, reply_markup=reply_markup)
        except Exception as e:
            print(f"[Telegram] Failed to send message: {e}")

    @classmethod
    def get_image_for_text(cls, text: str) -> str | None:
        from agents.persona_agent import PersonaAgent
        return PersonaAgent.get_image_for_mood(text, blocking=WAIT_FOR_MOOD_IMAGE)

    # ------------------------------------------------------------------ #
    #  Inbound command handling                                            #
    # ------------------------------------------------------------------ #

    @classmethod
    def _handle_text(cls, message):
        from agents.agent_service import AgentService
        from agents.persona_agent import PersonaAgent
        from agents.memory_service import MemoryService

        query = message.text or ''
        print(f"[Telegram] Received: '{query}'")
        lower = query.lower().strip()

        # Memory management commands
        if lower in ("memories", "show memories", "what do you remember"):
            memories = MemoryService.get_all()
            if not memories:
                cls.send_message("I don't remember anything yet~")
            else:
                lines = ["Here's what I remember~"]
                for i, m in enumerate(memories, 1):
                    lines.append(f"{i}. [{m['source']}] {m['content']}")
                cls.send_message("\n".join(lines))
            return

        if re.match(r'^(forget everything|clear memories)$', lower):
            MemoryService.clear()
            cls.send_message("All memories wiped~")
            return

        forget_one = re.match(r'^forget\s+(?:#|number\s+)?(\d+)$', lower)
        if forget_one:
            idx = int(forget_one.group(1))
            try:
                MemoryService.remove_at(idx)
                cls.send_message(f"Forgotten memory #{idx}~")
            except IndexError:
                cls.send_message(f"I don't have a memory #{idx}.")
            return

        # Normal message handling
        history = cls._format_history()

        # Resolve current mood once so both text generation and image selection are consistent
        from agents.persona_states import MOOD_MODIFIERS
        state_data = PersonaAgent.get_current_state()
        current_key = state_data.get('state', '')
        parts = current_key.rsplit('_', 1)
        mood = parts[1] if len(parts) == 2 and parts[1] in MOOD_MODIFIERS else None

        result = AgentService.handle_query(query)
        print(f"[Telegram] Agent result: {result!r}")
        if result:
            reply = PersonaAgent.generate_factual_relay(query, result, history=history, mood=mood)
        else:
            reply = PersonaAgent.generate_open_answer(query, history=history, mood=mood)
        cls._history.append((query, reply))
        cls.send_message(reply, photo=cls.get_image_for_text(reply))

        # Extract and store memory in background (non-blocking)
        exchange = f"User: {query}\nPersona: {reply}"
        threading.Thread(target=MemoryService.extract_from_exchange, args=(exchange,), daemon=True).start()

    @classmethod
    def _handle_callback(cls, call):
        data = call.data
        action = cls._pending_actions.pop(data, None)
        try:
            if action and data != 'dismiss':
                action()
            # Remove the inline keyboard from the original message
            cls._bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None,
            )
            cls._bot.answer_callback_query(call.id)
            if action and data != 'dismiss':
                cls.send_message("Done~")
        except Exception as e:
            print(f"[Telegram] Callback action error: {e}")
            try:
                cls._bot.answer_callback_query(call.id, text="Something went wrong.")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Polling                                                             #
    # ------------------------------------------------------------------ #

    @classmethod
    def _run_polling(cls):
        while True:
            try:
                cls._bot.polling(non_stop=True, timeout=30)
            except Exception as e:
                print(f"[Telegram] Polling error: {e}, retrying in 15s")
                time.sleep(15)
