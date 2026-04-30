"""Instagram Graph API integration for comment management.

IG Business/Creator accounts are accessed via a linked Facebook Page.
Tokens are stored under a key like `instagram_<account_name>` — the
default lookup uses `instagram_primary`.

The access_token is actually the linked Page's token — IG inherits from FB.
extra_data must contain: ig_user_id, linked_page_id, linked_page_name, account_name
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from vidpost.db import get_auth_token

GRAPH_API = "https://graph.facebook.com/v21.0"


class InstagramPlatform:
    name = "instagram"

    def __init__(self, account: str | None = None):
        self.token_key = f"instagram_{account.lower()}" if account else "instagram_primary"

    def _auth(self) -> tuple[str, str, str]:
        token_data = get_auth_token(self.token_key)
        if not token_data:
            raise RuntimeError(f"Not authenticated with Instagram ({self.token_key}).")
        extra = token_data["extra_data"]
        return token_data["access_token"], extra["ig_user_id"], extra.get("account_name", "")

    async def get_pending_comments(self, days: int = 14, limit_media: int = 50) -> list[dict[str, Any]]:
        """Comments on recent IG media that we haven't replied to yet."""
        access_token, ig_id, _ = self._auth()
        since_dt = datetime.now(timezone.utc) - timedelta(days=days)

        pending = []
        async with httpx.AsyncClient(timeout=60) as client:
            # Find our IG username so we can detect our own replies
            me = await client.get(f"{GRAPH_API}/{ig_id}", params={
                "fields": "username",
                "access_token": access_token,
            })
            my_username = me.json().get("username", "") if me.status_code == 200 else ""

            media_r = await client.get(f"{GRAPH_API}/{ig_id}/media", params={
                "fields": "id,caption,media_type,timestamp,permalink",
                "limit": limit_media, "access_token": access_token,
            })
            if media_r.status_code != 200:
                return []
            media_list = media_r.json().get("data", [])
            # Filter by date
            media_list = [m for m in media_list if m.get("timestamp") and _parse_ig_time(m["timestamp"]) >= since_dt]

            for m in media_list:
                c_r = await client.get(f"{GRAPH_API}/{m['id']}/comments", params={
                    "fields": "id,username,text,timestamp,replies{id,username,text}",
                    "limit": 100, "access_token": access_token,
                })
                if c_r.status_code != 200:
                    continue
                for c in c_r.json().get("data", []):
                    if c.get("username") == my_username:
                        continue
                    replies = (c.get("replies") or {}).get("data", [])
                    if any(r.get("username") == my_username for r in replies):
                        continue
                    if not c.get("text"):
                        continue
                    pending.append({
                        "comment_id": c["id"],
                        "post_id": m["id"],
                        "post_title": (m.get("caption") or "").strip().split("\n")[0][:80],
                        "post_caption": (m.get("caption") or "").strip()[:120],
                        "post_permalink": m.get("permalink", ""),
                        "author": c.get("username", "(anon)"),
                        "message": c["text"],
                        "time": c.get("timestamp", ""),
                    })
        return pending

    async def reply_to_comment(self, comment_id: str, message: str) -> dict[str, Any]:
        access_token, _, _ = self._auth()
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{GRAPH_API}/{comment_id}/replies",
                data={"message": message, "access_token": access_token},
            )
            try:
                return r.json()
            except Exception:
                return {"error": {"message": r.text, "code": r.status_code}}

    async def delete_comment(self, comment_id: str) -> bool:
        """Delete a comment (typically one you posted)."""
        access_token, _, _ = self._auth()
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(
                f"{GRAPH_API}/{comment_id}",
                params={"access_token": access_token},
            )
            return r.status_code == 200 and r.json().get("success", False)


def _parse_ig_time(s: str) -> datetime:
    import re
    s = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', s.replace('Z', '+00:00'))
    return datetime.fromisoformat(s)
