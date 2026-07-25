"""
Thin wrapper around Roblox's Open Cloud v2 Group Membership API.

Uses curl_cffi instead of aiohttp: Roblox's edge appears to silently drop connections
from Python's native TLS stack (aiohttp/requests) while accepting real curl requests to
the exact same IP -- likely TLS/HTTP fingerprinting on Roblox's side. curl_cffi wraps
actual libcurl under the hood, so it presents the same fingerprint as a real curl command,
which is what diagnostic testing confirmed works reliably.

NOTE: This is a Roblox BETA API as of mid-2026 -- field names and behavior could change.
Some developers have reported 401 errors ranking members via API keys even with correct
group:write scope (see Roblox DevForum). Test carefully with a low-stakes rank change
before relying on this for real promotions/demotions. If it stops working, check
https://create.roblox.com/docs/cloud/reference/features/groups for schema changes.
"""

from curl_cffi.requests import AsyncSession

USERNAME_LOOKUP_URL = "https://users.roblox.com/v1/usernames/users"
CLOUD_BASE = "https://apis.roblox.com/cloud/v2"


async def resolve_username_to_id(username: str):
    """Looks up a Roblox user ID from a username. Returns int or None if not found."""
    try:
        async with AsyncSession() as session:
            resp = await session.post(
                USERNAME_LOOKUP_URL,
                json={"usernames": [username], "excludeBannedUsers": True},
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"ROBLOX_LOOKUP: unexpected status {resp.status_code} for username '{username}': {resp.text[:300]}")
                return None
            data = resp.json()
            users = data.get("data", [])
            if not users:
                print(f"ROBLOX_LOOKUP: no match for username '{username}' (Roblox returned an empty result -- likely a typo or wrong spelling)")
                return None
            return users[0].get("id")
    except Exception as e:
        print(f"ROBLOX_LOOKUP: request failed for username '{username}': {type(e).__name__}: {e}")
        return None


async def get_membership_id(group_id: str, roblox_user_id: int, api_key: str):
    """Finds this user's membership resource ID within the group. Returns (membership_id, status_code).
    membership_id is None if not found or the request failed."""
    headers = {"x-api-key": api_key}
    url = f"{CLOUD_BASE}/groups/{group_id}/memberships"
    params = {"filter": f"user == 'users/{roblox_user_id}'", "maxPageSize": 1}
    try:
        async with AsyncSession() as session:
            resp = await session.get(url, headers=headers, params=params, timeout=10)
            status = resp.status_code
            if status != 200:
                return None, status
            data = resp.json()
            memberships = data.get("groupMemberships") or data.get("memberships") or []
            if not memberships:
                return None, status
            entry = memberships[0]
            # Field name for the resource identifier isn't fully settled in the beta docs --
            # try the likely candidates.
            membership_id = entry.get("id") or entry.get("path") or entry.get("name")
            if membership_id and "/" in str(membership_id):
                membership_id = str(membership_id).rsplit("/", 1)[-1]
            return membership_id, status
    except Exception as e:
        print(f"ROBLOX_MEMBERSHIP: request failed for user {roblox_user_id}: {type(e).__name__}: {e}")
        return None, None


async def assign_role(group_id: str, membership_id: str, roleset_id: int, api_key: str):
    """Sets a member's role in the group. Returns (success: bool, status_code, response_text)."""
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    url = f"{CLOUD_BASE}/groups/{group_id}/memberships/{membership_id}:assignRole"
    body = {"role": f"groups/{group_id}/roles/{roleset_id}"}
    try:
        async with AsyncSession() as session:
            resp = await session.post(url, headers=headers, json=body, timeout=10)
            return resp.status_code in (200, 204), resp.status_code, resp.text
    except Exception as e:
        return False, None, str(e)
