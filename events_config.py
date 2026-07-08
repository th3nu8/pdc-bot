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
    """Returns the clearance hierarchy, ordered lowest -> highest as listed in clearance.json."""
    return _load_json(CLEARANCE_FILE)


def clearance_names():
    return [entry.get("level", "") for entry in load_clearance() if entry.get("level")]


def _clearance_index_by_name(level_name, clearance_list):
    target = level_name.strip().lower()
    for i, entry in enumerate(clearance_list):
        if str(entry.get("level", "")).strip().lower() == target:
            return i
    return None


def _member_max_clearance_index(member, clearance_list):
    """Highest clearance index this member holds a role for, or -1 if none."""
    member_role_ids = {r.id for r in member.roles}
    best = -1
    for i, entry in enumerate(clearance_list):
        role_id = entry.get("role_id")
        if not role_id or role_id == "PUT_ROLE_ID_HERE":
            continue
        try:
            if int(role_id) in member_role_ids:
                best = max(best, i)
        except ValueError:
            continue
    return best


def member_has_clearance(member, required_level_name, clearance_list=None):
    """True if member holds a role at required_level_name or any higher level in the hierarchy."""
    if clearance_list is None:
        clearance_list = load_clearance()
    required_idx = _clearance_index_by_name(required_level_name, clearance_list)
    if required_idx is None:
        return False
    member_idx = _member_max_clearance_index(member, clearance_list)
    return member_idx >= required_idx
