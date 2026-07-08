import json
import os

EVENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.json")
CLEARANCE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clearance.json")


def _load_json(path):
    """Reads a JSON file fresh every call, so edits take effect without a bot restart."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return data


def load_events():
    return _load_json(EVENTS_FILE)


def find_event(name):
    """Case-insensitive lookup of an event type entry by name."""
    target = name.strip().lower()
    for entry in load_events():
        if str(entry.get("name", "")).strip().lower() == target:
            return entry
    return None


def event_names():
    return [entry.get("name", "") for entry in load_events() if entry.get("name")]


def load_clearance():
    """Returns the clearance list: [{level: int, name: str, role_id: str}, ...]."""
    return _load_json(CLEARANCE_FILE)


def get_clearance_name(level_number, clearance_list=None):
    if clearance_list is None:
        clearance_list = load_clearance()
    for entry in clearance_list:
        if entry.get("level") == level_number:
            return entry.get("name")
    return None


def member_clearance_level(member, clearance_list=None):
    """Highest numeric clearance level this member holds a role for, or 0 if none."""
    if clearance_list is None:
        clearance_list = load_clearance()
    member_role_ids = {r.id for r in member.roles}
    best = 0
    for entry in clearance_list:
        role_id = entry.get("role_id")
        level = entry.get("level")
        if not role_id or role_id == "PUT_ROLE_ID_HERE" or level is None:
            continue
        try:
            if int(role_id) in member_role_ids:
                best = max(best, int(level))
        except (ValueError, TypeError):
            continue
    return best


def member_has_clearance(member, required_level, clearance_list=None):
    """True if member's highest clearance level is >= required_level (higher levels can do everything lower ones can)."""
    if clearance_list is None:
        clearance_list = load_clearance()
    try:
        required_level_int = int(required_level)
    except (TypeError, ValueError):
        return False
    return member_clearance_level(member, clearance_list) >= required_level_int
