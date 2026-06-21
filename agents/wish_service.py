import json
import os
from datetime import datetime

MAX_REJECTED = 50


class WishService:
    WISHES_PATH = os.path.join('env', 'persona_wishes.json')
    MAX_WISHES = 100

    @classmethod
    def _load(cls) -> dict:
        """Load wishes file. Handles legacy flat-list format gracefully."""
        try:
            with open(cls.WISHES_PATH) as f:
                data = json.load(f)
            if isinstance(data, list):
                # Legacy format — migrate in place
                return {"wishes": data, "rejected": []}
            return {
                "wishes":   data.get("wishes", []),
                "rejected": data.get("rejected", []),
            }
        except FileNotFoundError:
            return {"wishes": [], "rejected": []}
        except Exception as e:
            print(f"[Wishes] Could not read {cls.WISHES_PATH}: {e}")
            return {"wishes": [], "rejected": []}

    @classmethod
    def _save(cls, data: dict):
        os.makedirs(os.path.dirname(cls.WISHES_PATH), exist_ok=True)
        with open(cls.WISHES_PATH, 'w') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def add(cls, content: str, state_context: str | None = None, theme: str | None = None):
        data = cls._load()
        entry = {
            "content": content,
            "timestamp": datetime.now().isoformat(timespec='seconds'),
            "state_context": state_context,
            "theme": theme,
        }
        data["wishes"].append(entry)
        if len(data["wishes"]) > cls.MAX_WISHES:
            data["wishes"] = data["wishes"][-cls.MAX_WISHES:]
        cls._save(data)
        print(f"[Wishes] Stored: {content}")

    @classmethod
    def get_all(cls) -> list[dict]:
        return cls._load()["wishes"]

    @classmethod
    def mark_cemented(cls, index: int):
        """Mark wish at 1-based index as cemented."""
        data = cls._load()
        wishes = data["wishes"]
        if index < 1 or index > len(wishes):
            raise IndexError(f"No wish at index {index}")
        wishes[index - 1]["cemented"] = True
        cls._save(data)

    @classmethod
    def mark_resolved(cls, index: int, answer: str, memory: str):
        """Mark wish at 1-based index as resolved with the user's answer and generated memory."""
        data = cls._load()
        wishes = data["wishes"]
        if index < 1 or index > len(wishes):
            raise IndexError(f"No wish at index {index}")
        wishes[index - 1].update({"resolved": True, "resolved_answer": answer, "resolved_memory": memory})
        cls._save(data)

    @classmethod
    def get_rejected_texts(cls) -> list[str]:
        return cls._load()["rejected"]

    @classmethod
    def remove_at(cls, index: int):
        """Reject wish at 1-based index — moves content to rejected list."""
        data = cls._load()
        wishes = data["wishes"]
        if index < 1 or index > len(wishes):
            raise IndexError(f"No wish at index {index}")
        removed = wishes.pop(index - 1)
        data["rejected"].append(removed["content"])
        if len(data["rejected"]) > MAX_REJECTED:
            data["rejected"] = data["rejected"][-MAX_REJECTED:]
        cls._save(data)
        print(f"[Wishes] Rejected: {removed['content']}")

    @classmethod
    def clear(cls):
        data = cls._load()
        data["wishes"] = []
        cls._save(data)
        print("[Wishes] All wishes cleared.")
