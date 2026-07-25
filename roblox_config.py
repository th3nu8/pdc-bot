import json
import os

RANKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roblox_ranks.json")


def load_ranks():
    """Reads roblox_ranks.json fresh every call, so edits take effect without a bot restart."""
    if not os.path.exists(RANKS_FILE):
        return []
    try:
        with open(RANKS_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return data


def best_rank_for_member(member) -> dict:
    """Returns the highest-level rank entry whose discord_role_id this member currently holds,
    or None if they hold none of the mapped roles."""
    member_role_ids = {r.id for r in member.roles}
    best = None
    for entry in load_ranks():
        role_id = entry.get("discord_role_id")
        level = entry.get("level")
        if not role_id or role_id == "PUT_ROLE_ID_HERE" or level is None:
            continue
        try:
            if int(role_id) in member_role_ids:
                if best is None or level > best.get("level", -1):
                    best = entry
        except (ValueError, TypeError):
            continue
    return best


def all_mapped_role_ids() -> set:
    """Every discord_role_id referenced anywhere in roblox_ranks.json, as ints."""
    ids = set()
    for entry in load_ranks():
        role_id = entry.get("discord_role_id")
        if role_id and role_id != "PUT_ROLE_ID_HERE":
            try:
                ids.add(int(role_id))
            except (ValueError, TypeError):
                pass
    return ids
