import json
import subprocess
import threading
import atexit
import time
import shutil
import os
import requests

OLLAMA_BASE_URL = "http://localhost:11434"
# OLLAMA_MODEL = "qwen3.5:9b"
OLLAMA_MODEL = "huihui_ai/qwen3.5-abliterated:9b"

_process = None
_call_lock = threading.Lock()  # serialises concurrent Ollama requests

# Passing tool definitions shifts Qwen3 into a more focused reasoning mode.
# Multiple plausible tools increase the likelihood the model commits to tool-selection
# reasoning rather than open-ended CoT. tool_choice is not forced — if the model calls
# any tool we extract its text argument where applicable.
_DUMMY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "respond",
            "description": "Send a text response to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Your response text"}
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather and forecast data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of forecast days (1–7)"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": "Fetch upcoming calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "description": "How many days ahead to look"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_lights",
            "description": "Control the smart home lights.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["on", "off", "rainbow"]},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_memories",
            "description": "Retrieve stored facts and preferences about the user.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def start():
    _start_server()
    if _wait_for_ready():
        threading.Thread(target=_ensure_model, daemon=True).start()
    else:
        print("WARNING: Ollama did not become ready — persona quotes will use fallback text.")


def _start_server():
    global _process
    try:
        _process = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        if _process.poll() is not None:
            _process = None
            print("Ollama: using existing instance.")
        else:
            atexit.register(_stop)
            print("Ollama: started.")
    except FileNotFoundError:
        print("Ollama: not found in PATH, assuming already running.")
    except Exception as e:
        print(f"WARNING: Could not start ollama: {e}")


def _wait_for_ready(timeout: int = 15) -> bool:
    """Poll until the Ollama HTTP endpoint responds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(f"{OLLAMA_BASE_URL}/", timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _ensure_model():
    """Pull the configured model if it is not already downloaded."""
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        installed = [m["name"].split(":")[0] for m in resp.json().get("models", [])]
        model_base = OLLAMA_MODEL.split(":")[0]
        if model_base in installed:
            print(f"Ollama: model '{OLLAMA_MODEL}' already available.")
            return
        print(f"Ollama: pulling model '{OLLAMA_MODEL}' (this may take a few minutes)...")
        requests.post(
            f"{OLLAMA_BASE_URL}/api/pull",
            json={"name": OLLAMA_MODEL, "stream": False},
            timeout=600,
        )
        print(f"Ollama: model '{OLLAMA_MODEL}' ready.")
    except Exception as e:
        print(f"Ollama: model check/pull failed: {e}")


def _call(prompt: str, timeout: int = 10, *, system: str | None = None,
          skip_if_busy: bool = False, think: bool = False,
          use_tools: bool = False) -> str | None:
    """POST to Ollama /api/chat and return the response text, or None on failure.

    Called by llm_router.call_llm() — do not call directly.
    use_tools: pass a dummy tool definition to shift Qwen3 into a faster, more focused
    reasoning mode. If the model calls the tool, the text argument is used as the answer.
    """
    acquired = _call_lock.acquire(blocking=not skip_if_busy)
    if not acquired:
        print(f"[Ollama] _call: skipped (busy) think={think} prompt={prompt[:60]!r}")
        return None
    try:
        print(f"[Ollama] call → think={think} tools={use_tools} timeout={timeout}s prompt={prompt[:80]!r}")
        t0 = time.time()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        options = (
            {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.5}
            if think else
            {"temperature": 0.7, "top_p": 0.8,  "top_k": 20}
        )
        body = {"model": OLLAMA_MODEL, "messages": messages, "stream": True, "think": think, "options": options}
        if use_tools:
            body["tools"] = _DUMMY_TOOLS

        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=body,
            timeout=timeout,
            stream=True,
        )
        resp.raise_for_status()

        deadline = time.time() + timeout
        content = []
        thinking_chars = 0
        first_chunk_at = None
        for line in resp.iter_lines():
            now = time.time()
            if now > deadline:
                print(f"[Ollama] deadline hit — thinking: {thinking_chars} chars, answer: {len(''.join(content))} chars")
                break
            if not line:
                continue
            try:
                chunk = json.loads(line)
                msg = chunk.get("message", {})
                if first_chunk_at is None:
                    first_chunk_at = now
                    print(f"[Ollama] first chunk at {now-t0:.1f}s")
                thinking_chars += len(msg.get("thinking") or "")
                if c := msg.get("content", ""):
                    content.append(c)
                # If the model called the dummy tool, extract the text argument as the answer.
                if not content:
                    for tc in msg.get("tool_calls") or []:
                        if text := tc.get("function", {}).get("arguments", {}).get("text", ""):
                            content.append(text)
                if chunk.get("done"):
                    if think and thinking_chars:
                        print(f"[Ollama] thinking: {thinking_chars} chars")
                    break
            except json.JSONDecodeError:
                pass

        answer = ''.join(content).strip() or None
        print(f"[Ollama] call ← {time.time()-t0:.1f}s | answer={(answer[:80] if answer else None)!r}")
        return answer
    except Exception as e:
        print(f"[Ollama] call failed: {e}")
        return None
    finally:
        _call_lock.release()


def _collect_thinking(prompt: str, think_budget_chars: int = 4000, timeout: int = 60, *,
                      system: str | None = None, skip_if_busy: bool = False) -> str | None:
    """Phase-1 of a two-phase call: stream think=True, collect thinking up to budget, then stop.

    Called by llm_router.collect_thinking() — do not call directly.
    Returns the raw thinking text (not the answer).
    """
    acquired = _call_lock.acquire(blocking=not skip_if_busy)
    if not acquired:
        print(f"[Ollama] _collect_thinking: skipped (busy)")
        return None
    try:
        print(f"[Ollama] collect_thinking → budget={think_budget_chars} timeout={timeout}s prompt={prompt[:80]!r}")
        t0 = time.time()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": True, "think": True,
                  "options": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.5}},
            timeout=timeout,
            stream=True,
        )
        resp.raise_for_status()

        deadline = time.time() + timeout
        thinking = []
        thinking_chars = 0
        first_chunk_at = None
        last_progress_log = t0
        for line in resp.iter_lines():
            now = time.time()
            if now > deadline:
                print(f"[Ollama] think budget deadline hit at {thinking_chars} chars")
                break
            if not line:
                continue
            try:
                chunk = json.loads(line)
                msg = chunk.get("message", {})
                if first_chunk_at is None and (msg.get("thinking") or msg.get("content")):
                    first_chunk_at = now
                    print(f"[Ollama] first chunk at {now-t0:.1f}s")
                if t := msg.get("thinking", ""):
                    thinking.append(t)
                    thinking_chars += len(t)
                    if now - last_progress_log >= 10:
                        print(f"[Ollama] thinking... {thinking_chars}/{think_budget_chars} chars at {now-t0:.1f}s")
                        last_progress_log = now
                    if thinking_chars >= think_budget_chars:
                        print(f"[Ollama] think budget reached: {thinking_chars} chars at {now-t0:.1f}s")
                        break
                if msg.get("content", ""):
                    print(f"[Ollama] answer started at {now-t0:.1f}s — stopping think phase ({thinking_chars} chars collected)")
                    break
                if chunk.get("done"):
                    break
            except json.JSONDecodeError:
                pass

        resp.close()
        result = ''.join(thinking).strip()
        print(f"[Ollama] collect_thinking ← {time.time()-t0:.1f}s | {thinking_chars} chars")
        return result or None
    except Exception as e:
        print(f"[Ollama] collect_thinking failed: {e}")
        return None
    finally:
        _call_lock.release()


def _stop():
    if _process:
        _process.terminate()
        _process.wait()
        print("Ollama: stopped.")
