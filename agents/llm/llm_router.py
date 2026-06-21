"""LLM backend router — the only file that knows which backend is active.

All callers import call_llm() and collect_thinking() from here.
To add a new backend: add a branch in each function below and implement
the backend in its own module under agents/llm/.
"""
import config


def start_backend() -> None:
    """Start or verify the configured LLM backend."""
    if config.Config.LLM_BACKEND == 'lmstudio':
        from agents.llm.lmstudio_service import check_ready
        check_ready()
    else:
        from agents.llm.ollama_service import start
        start()


def call_llm(prompt: str, timeout: int = 10, *, system: str | None = None,
             skip_if_busy: bool = False, think: bool = False,
             use_tools: bool = False) -> str | None:
    """Send a prompt to the active LLM backend and return the response, or None on failure.

    skip_if_busy: return None immediately if another call is in flight.
                  Use for low-priority callers with a fallback (quotes, suggestions).
    use_tools: Ollama-only — passes dummy tool definitions to shift Qwen3 into a
               faster reasoning mode. Ignored by other backends.
    """
    if config.Config.LLM_BACKEND == 'lmstudio':
        from agents.llm.lmstudio_service import _call
        return _call(prompt, timeout, system=system, skip_if_busy=skip_if_busy, think=think)
    from agents.llm.ollama_service import _call
    return _call(prompt, timeout, system=system, skip_if_busy=skip_if_busy, think=think, use_tools=use_tools)


def collect_thinking(prompt: str, think_budget_chars: int = 4000, timeout: int = 60, *,
                     system: str | None = None, skip_if_busy: bool = False) -> str | None:
    """Phase-1 of a two-phase call: stream think=True, collect reasoning up to budget, then stop.

    Returns the raw thinking text (not the answer). The caller uses this as context
    for a fast follow-up call with think=False via call_llm().
    """
    if config.Config.LLM_BACKEND == 'lmstudio':
        from agents.llm.lmstudio_service import _collect_thinking
        return _collect_thinking(prompt, think_budget_chars, timeout, system=system, skip_if_busy=skip_if_busy)
    from agents.llm.ollama_service import _collect_thinking
    return _collect_thinking(prompt, think_budget_chars, timeout, system=system, skip_if_busy=skip_if_busy)
