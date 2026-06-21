import json
import threading
import time
import requests

LM_STUDIO_BASE_URL = "http://localhost:1234/v1"
# LM_STUDIO_MODEL = "qwen3.5-9b"  # match the model identifier shown in LM Studio
LM_STUDIO_MODEL = "huihui-qwen3.5-9b-claude-4.6-opus-abliterated"  # match the model identifier shown in LM Studio
MODEL_TEMPERATURE = 1.3
# High ceiling so the model can finish its thinking block and still produce an answer.
# Tune down once thinking verbosity is understood.
MAX_TOKENS = 12000

_call_lock = threading.Lock()


def check_ready():
    try:
        requests.get(f"{LM_STUDIO_BASE_URL}/models", timeout=3)
        print("LM Studio: reachable.")
    except Exception:
        print("WARNING: LM Studio not reachable at startup — LLM calls will fail.")


def _split_thinking(raw: str) -> tuple[str | None, str, bool]:
    """Return (thinking, answer, ended_cleanly).

    thinking: content inside <think>...</think>, or None if no clean block found.
    answer:   content outside the think block, or the full raw if no clean block found.
    ended_cleanly: True only when both <think> and </think> are present.
    """
    think_start = raw.find('<think>')
    if think_start == -1:
        return None, raw, True
    think_end = raw.find('</think>', think_start)
    if think_end == -1:
        # No closing tag — can't determine boundary; treat everything as answer
        return None, raw, False
    thinking = raw[think_start + 7:think_end].strip()
    answer = (raw[:think_start] + raw[think_end + 8:]).strip()
    return thinking, answer, True


def collect_thinking(prompt: str, think_budget_chars: int = 4000, timeout: int = 60, *,
                     system: str | None = None, skip_if_busy: bool = False) -> str | None:
    """Stream a think=True call, collect thinking up to budget, then close.

    Returns the raw thinking text, or None on failure/timeout.
    """
    acquired = _call_lock.acquire(blocking=not skip_if_busy)
    if not acquired:
        print(f"[LMStudio] collect_thinking: skipped (busy)")
        return None
    try:
        print(f"[LMStudio] collect_thinking → budget={think_budget_chars} timeout={timeout}s prompt={prompt[:60]!r}")
        t0 = time.time()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = requests.post(
            f"{LM_STUDIO_BASE_URL}/chat/completions",
            json={"model": LM_STUDIO_MODEL, "messages": messages, "stream": True,
                  "temperature": MODEL_TEMPERATURE, "max_tokens": MAX_TOKENS},
            timeout=timeout,
            stream=True,
        )
        resp.raise_for_status()

        deadline = time.time() + timeout
        buf = []          # raw accumulated content
        buf_str = ""
        in_think = False
        thinking_start = 0
        thinking_chars = 0
        total_chars = 0
        first_chunk_at = None
        last_progress_log = t0

        for line in resp.iter_lines():
            now = time.time()
            if now > deadline:
                print(f"[LMStudio] think budget deadline hit at {thinking_chars} thinking chars | {total_chars} total chars in buf")
                break
            if not line:
                continue
            if isinstance(line, bytes):
                line = line.decode('utf-8')
            if not line.startswith('data: '):
                continue
            data = line[6:]
            if data.strip() == '[DONE]':
                print(f"[LMStudio] stream [DONE] at {now-t0:.1f}s | total={total_chars} chars")
                break
            try:
                chunk = json.loads(data)
                delta = chunk['choices'][0]['delta'].get('content', '')
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                print(f"[LMStudio] parse error: {e} | raw={data[:120]!r}")
                continue
            if not delta:
                continue

            if first_chunk_at is None:
                first_chunk_at = now
                print(f"[LMStudio] first chunk at {now-t0:.1f}s")

            buf.append(delta)
            buf_str = ''.join(buf)
            total_chars += len(delta)

            if not in_think:
                idx = buf_str.find('<think>')
                if idx != -1:
                    in_think = True
                    thinking_start = idx + 7
                    print(f"[LMStudio] <think> found at {now-t0:.1f}s | buf so far={total_chars} chars | prefix={buf_str[:idx].strip()!r}")
                elif now - last_progress_log >= 10:
                    print(f"[LMStudio] waiting for <think> at {now-t0:.1f}s | {total_chars} chars so far | tail={buf_str[-100:]!r}")
                    last_progress_log = now
            else:
                thinking_chars = len(buf_str) - thinking_start
                end_idx = buf_str.find('</think>', thinking_start)
                if end_idx != -1:
                    thinking_chars = end_idx - thinking_start
                    print(f"[LMStudio] </think> found at {now-t0:.1f}s | {thinking_chars} thinking chars collected (clean end)")
                    break
                if thinking_chars >= think_budget_chars:
                    print(f"[LMStudio] think budget reached: {thinking_chars} chars at {now-t0:.1f}s")
                    break
                if now - last_progress_log >= 10:
                    print(f"[LMStudio] thinking... {thinking_chars}/{think_budget_chars} chars at {now-t0:.1f}s")
                    last_progress_log = now

        resp.close()
        elapsed = time.time() - t0
        if in_think:
            end_idx = buf_str.find('</think>', thinking_start)
            result = buf_str[thinking_start:end_idx if end_idx != -1 else None].strip()
            print(f"[LMStudio] collect_thinking ← {elapsed:.1f}s | {len(result)} chars")
            return result or None
        print(f"[LMStudio] collect_thinking ← {elapsed:.1f}s | no <think> block found | total={total_chars} chars | buf={buf_str[:300]!r}")
        return None
    except Exception as e:
        print(f"[LMStudio] collect_thinking failed: {e}")
        return None
    finally:
        _call_lock.release()


def call_lmstudio(prompt: str, timeout: int = 10, *, system: str | None = None,
                  skip_if_busy: bool = False, think: bool = False) -> str | None:
    acquired = _call_lock.acquire(blocking=not skip_if_busy)
    if not acquired:
        print(f"[LMStudio] call: skipped (busy) think={think} prompt={prompt[:60]!r}")
        return None
    try:
        print(f"[LMStudio] call → think={think} reasoning_effort={'on' if think else 'none'} timeout={timeout}s prompt={prompt[:60]!r}")
        t0 = time.time()
        messages = []
        if system:
            if not think:
                system += "/nothink"
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {"model": LM_STUDIO_MODEL, "messages": messages, "stream": True, "temperature": MODEL_TEMPERATURE, "max_tokens": MAX_TOKENS}
        if not think:
            body["reasoning_effort"] = "none"
            
        resp = requests.post(
            f"{LM_STUDIO_BASE_URL}/chat/completions",
            json=body,
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
        raw = ''.join(content).strip()
        print(raw)
        thinking_text, answer, ended_cleanly = _split_thinking(raw)

        if thinking_text is not None:
            print(f"[LMStudio] thinking: {len(thinking_text)} chars")
            if think:
                print(f"[LMStudio] think:\n{thinking_text}")
        elif not ended_cleanly:
            print(f"[LMStudio] WARNING: no </think> found — returning full response as answer ({len(raw)} chars)")

        if not answer:
            print(f"[LMStudio] call ← {time.time()-t0:.1f}s | answer=None (empty after thinking split, raw={len(raw)} chars)")
        else:
            print(f"[LMStudio] call ← {time.time()-t0:.1f}s | answer={answer[:80]!r}")
        return answer or None
    except Exception as e:
        print(f"[LMStudio] call failed: {e}")
        return None
    finally:
        _call_lock.release()
