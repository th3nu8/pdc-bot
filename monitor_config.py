import json
import os

MONITOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor_sites.json")


def load_sites():
    """Reads monitor_sites.json fresh every call, so edits take effect without a bot restart."""
    if not os.path.exists(MONITOR_FILE):
        return []
    try:
        with open(MONITOR_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return data
