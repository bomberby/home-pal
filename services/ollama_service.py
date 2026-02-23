import subprocess
import threading
import atexit
import time
import shutil
import os
import requests

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2"

_process = None


def start():
    """Start ollama serve if not already running, then ensure the model is available."""
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


def _stop():
    if _process:
        _process.terminate()
        _process.wait()
        print("Ollama: stopped.")
