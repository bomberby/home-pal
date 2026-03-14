"""Base chat service — shared pipeline for all chat frontends (desktop widget, Telegram, etc.).

Responsibilities:
  - Intent routing via AgentService
  - Reply generation via PersonaAgent
  - Memory extraction (fires in background before returning, before any image
    resolution the caller may trigger — avoids the _in_progress GPU-check race)

Returns {'reply': str, 'mood': str | None}.
Image resolution is the caller's responsibility.
"""

import threading


class ChatService:
    @classmethod
    def handle(cls, query: str, history: list | None = None) -> dict:
        from agents.persona.agent import PersonaAgent
        from agents.persona.states import MOOD_MODIFIERS

        history_str = cls._build_history_str(history or [])

        mood = None
        try:
            state_key = PersonaAgent.get_current_state().get('state', '')
            parts = state_key.rsplit('_', 1)
            if len(parts) == 2 and parts[1] in MOOD_MODIFIERS:
                mood = parts[1]
        except Exception:
            pass

        factual = None
        try:
            from agents.agent_service import AgentService
            factual = AgentService.handle_query(query)
            print(f'[ChatService] query={query!r} → agent={factual!r}')
        except Exception as e:
            print(f'[ChatService] AgentService error: {e}')

        try:
            if factual:
                print('[ChatService] → factual relay')
                reply = PersonaAgent.generate_factual_relay(query, factual, history_str, mood)
            else:
                print('[ChatService] → open answer')
                reply = PersonaAgent.generate_open_answer(query, history_str, mood)
        except Exception as e:
            print(f'[ChatService] generation error: {e}')
            reply = "Sorry, something went wrong on my end."

        exchange = f"User: {query}\nPersona: {reply}"
        threading.Thread(target=cls._extract_memory, args=(exchange,), daemon=True).start()

        return {'reply': reply, 'mood': mood}

    @staticmethod
    def _build_history_str(history: list) -> str | None:
        lines = [
            f"User: {t[0]}\nYou: {t[1]}"
            for t in history[-6:]
            if isinstance(t, (list, tuple)) and len(t) == 2
        ]
        return '\n\n'.join(lines) if lines else None

    @staticmethod
    def _extract_memory(exchange: str) -> None:
        try:
            from agents.memory_service import MemoryService
            MemoryService.extract_from_exchange(exchange)
            MemoryService.extract_persona_from_exchange(exchange)
        except Exception as e:
            print(f'[ChatService] memory extraction error: {e}')
