import json
import threading
import requests

LM_STUDIO_BASE_URL = "http://localhost:1234/v1"
LM_STUDIO_MODEL = "qwen3.5-9b"  # match the model identifier shown in LM Studio
MODEL_TEMPERATURE = 1.3

_call_lock = threading.Lock()


def check_ready():
    try:
        requests.get(f"{LM_STUDIO_BASE_URL}/models", timeout=3)
        print("LM Studio: reachable.")
    except Exception:
        print("WARNING: LM Studio not reachable at startup — LLM calls will fail.")


def call_lmstudio(prompt: str, timeout: int = 10, *, system: str | None = None,
                  skip_if_busy: bool = False, think: bool = False) -> str | None:
    acquired = _call_lock.acquire(blocking=not skip_if_busy)
    if not acquired:
        return None
    try:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = requests.post(
            f"{LM_STUDIO_BASE_URL}/chat/completions",
            json={"model": LM_STUDIO_MODEL, "messages": messages, "stream": True, "temperature": MODEL_TEMPERATURE},
            timeout=timeout,
            stream=True,
        )
        resp.raise_for_status()
        content = []
        for line in resp.iter_lines():
            if not line:
                continue
            if isinstance(line, bytes):
                line = line.decode('utf-8')
            if not line.startswith('data: '):
                continue
            data = line[6:]
            if data.strip() == '[DONE]':
                break
            try:
                delta = json.loads(data)['choices'][0]['delta'].get('content', '')
                if delta:
                    content.append(delta)
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
        return ''.join(content).strip() or None
    except Exception as e:
        print(f"[LMStudio] Call failed: {e}")
        return None
    finally:
        _call_lock.release()
