import json
import os

DETACHMENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "detachments.json")


def load_detachments():
    """Reads detachments.json fresh every call, so edits take effect without a bot restart."""
    if not os.path.exists(DETACHMENTS_FILE):
        return []
    try:
        with open(DETACHMENTS_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return data


def find_detachment(name):
    """Case-insensitive lookup of a detachment entry by name."""
    target = name.strip().lower()
    for entry in load_detachments():
        if str(entry.get("name", "")).strip().lower() == target:
            return entry
    return None


def detachment_names():
    return [entry.get("name", "") for entry in load_detachments() if entry.get("name")]
