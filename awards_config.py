import json
import os

AWARDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "awards.json")


def load_awards():
    """Reads awards.json fresh every call, so edits take effect without a bot restart."""
    if not os.path.exists(AWARDS_FILE):
        return []
    try:
        with open(AWARDS_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return data


def find_award(name):
    """Case-insensitive lookup of an award entry by name."""
    target = name.strip().lower()
    for entry in load_awards():
        if str(entry.get("name", "")).strip().lower() == target:
            return entry
    return None


def award_names():
    return [entry.get("name", "") for entry in load_awards() if entry.get("name")]
