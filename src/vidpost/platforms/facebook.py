"""Meta Graph API integration for Facebook Page video posting.

Supports multiple pages. Pages are stored as:
  - facebook            (default page)
  - facebook_<name>     (additional pages, e.g. facebook_secondary)

Use --page flag to choose which page to post to.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from vidpost.config import get_platform_config
from vidpost.db import get_auth_token, save_auth_token
from vidpost.platforms.base import PlatformBase

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"
REELS_API_BASE = "https://graph.facebook.com/v25.0"
REELS_UPLOAD_BASE = "https://rupload.facebook.com/video-upload/v25.0"


class FacebookPlatform(PlatformBase):
    name = "facebook"

    def __init__(self, page: str | None = None):
        """Initialize with optional page name. e.g., 'secondary' uses 'facebook_secondary' token."""
        if page and page.lower() != "default":
            self.token_key = f"facebook_{page.lower()}"
        else:
            self.token_key = "facebook"

    async def authenticate(self) -> bool:
        """Save Page Access Tokens for one or more Pages the user manages.

        Accepts either a User token or a Page token. If a User token is given,
        we exchange it for a long-lived User token (when app_id+app_secret are
        configured) and then call /me/accounts to list Pages. Page tokens
        derived from a long-lived User token never expire — Page tokens
        derived from a short-lived User token expire in ~1 hour, which makes
        scheduled cron jobs fail unpredictably.
        """
        import click
        token = click.prompt("Enter your Facebook access token (User or Page token)")

        config = get_platform_config("facebook")
        app_id = config.get("app_id", "").strip()
        app_secret = config.get("app_secret", "").strip()

        async with httpx.AsyncClient(timeout=30) as client:
            # If app credentials configured, exchange for long-lived User token
            # (~60 days). Page tokens derived from this never expire.
            if app_id and app_secret:
                exch = await client.get(
                    f"{GRAPH_API_BASE}/oauth/access_token",
                    params={
                        "grant_type": "fb_exchange_token",
                        "client_id": app_id,
                        "client_secret": app_secret,
                        "fb_exchange_token": token,
                    },
                )
                if exch.status_code == 200 and "access_token" in exch.json():
                    token = exch.json()["access_token"]
                    click.echo("Exchanged for long-lived User token (60-day TTL).")
                else:
                    click.echo(f"Long-lived exchange failed ({exch.status_code}); using token as-is. "
                               f"Page tokens will likely expire within hours.")
            else:
                click.echo("No app_id/app_secret in config — skipping long-lived token exchange. "
                           "Page tokens will expire within hours and cron jobs will fail.")
                click.echo("Add 'facebook.app_id' and 'facebook.app_secret' to ~/.vidpost/config.yaml "
                           "and re-run this command to get never-expiring Page tokens.")

            r = await client.get(
                f"{REELS_API_BASE}/me/accounts",
                params={"access_token": token, "fields": "id,name,access_token,tasks"},
            )
            pages = r.json().get("data", []) if r.status_code == 200 else []

        if not pages:
            # Treat input as a Page token: ask for page_id/name and save as-is.
            click.echo("Could not list Pages from /me/accounts — assuming this is already a Page token.")
            page_id = click.prompt("Enter your Facebook Page ID")
            page_name = click.prompt("Enter a name for this page (e.g., default, secondary)")
            platform_key = f"facebook_{page_name.lower()}" if page_name.lower() != "default" else "facebook"
            save_auth_token(
                platform=platform_key,
                access_token=token,
                extra_data={"page_id": page_id, "page_name": page_name},
            )
            return True

        click.echo("Pages this token can manage:")
        for i, p in enumerate(pages, 1):
            tasks = ",".join(p.get("tasks") or [])
            click.echo(f"  {i}. {p['name']} (id={p['id']}) — {tasks}")
        click.echo(f"  {len(pages) + 1}. All of the above")
        choice = click.prompt("Pick a page", type=int, default=len(pages) + 1)

        selected = pages if choice == len(pages) + 1 else [pages[choice - 1]]
        for p in selected:
            page_name = click.prompt(
                f"Name for '{p['name']}' (used as token key)",
                default=p["name"].lower().replace(" ", ""),
            )
            platform_key = f"facebook_{page_name.lower()}" if page_name.lower() != "default" else "facebook"
            save_auth_token(
                platform=platform_key,
                access_token=p["access_token"],
                extra_data={"page_id": p["id"], "page_name": page_name},
            )
            click.echo(f"  saved {platform_key} -> {p['name']}")
        return True

    async def _reels_upload(
        self,
        video_path: Path,
        access_token: str,
        page_id: str,
    ) -> str:
        """Run steps 1 and 2 of the Reels 3-step publish flow.

        Returns the video_id that can be used for the finish step.
        """
        file_size = video_path.stat().st_size

        async with httpx.AsyncClient(timeout=600) as client:
            # Step 1: initialize upload session
            start_r = await client.post(
                f"{REELS_API_BASE}/{page_id}/video_reels",
                data={
                    "upload_phase": "start",
                    "access_token": access_token,
                },
            )
            start_r.raise_for_status()
            start_body = start_r.json()
            video_id = start_body.get("video_id")
            if not video_id:
                raise RuntimeError(f"Reels start did not return video_id: {start_body}")

            # Step 2: binary upload to rupload.facebook.com
            with open(video_path, "rb") as f:
                data = f.read()
            upload_r = await client.post(
                f"{REELS_UPLOAD_BASE}/{video_id}",
                headers={
                    "Authorization": f"OAuth {access_token}",
                    "offset": "0",
                    "file_size": str(file_size),
                    "Content-Type": "application/octet-stream",
                },
                content=data,
            )
            upload_r.raise_for_status()

        return video_id

    async def upload_video(self, video_path: Path, metadata: dict[str, Any]) -> str:
        """Publish a Reel to the Page immediately.

        All Page uploads go through the Reels API because:
          (a) source content is always vertical <=90s Reels, and
          (b) the regular /videos endpoint pushes posts into the Video feed,
              which gets significantly less organic reach than the Reels feed.
        """
        token_data = get_auth_token(self.token_key)
        if not token_data:
            raise RuntimeError(f"Not authenticated with Facebook ({self.token_key}). Run: vidpost auth facebook")

        from vidpost.captions.transforms import for_facebook

        page_id = token_data["extra_data"]["page_id"]
        access_token = token_data["access_token"]

        fb = for_facebook(metadata.get("caption", ""), metadata.get("hashtags", []))
        caption = fb["caption"]

        video_id = await self._reels_upload(video_path, access_token, page_id)

        # Step 3: finish + publish
        async with httpx.AsyncClient(timeout=600) as client:
            finish_r = await client.post(
                f"{REELS_API_BASE}/{page_id}/video_reels",
                params={
                    "access_token": access_token,
                    "video_id": video_id,
                    "upload_phase": "finish",
                    "video_state": "PUBLISHED",
                    "description": caption,
                },
            )
            finish_r.raise_for_status()

        return video_id

    async def schedule_video(self, video_path: Path, metadata: dict[str, Any], publish_at: str) -> str:
        """Schedule a Reel for future publication via the Reels API."""
        from vidpost.captions.transforms import for_facebook

        token_data = get_auth_token(self.token_key)
        if not token_data:
            raise RuntimeError(f"Not authenticated with Facebook ({self.token_key}). Run: vidpost auth facebook")

        page_id = token_data["extra_data"]["page_id"]
        access_token = token_data["access_token"]

        dt = datetime.fromisoformat(publish_at)
        if dt.tzinfo is None:
            from zoneinfo import ZoneInfo
            from vidpost.config import load_config
            tz = ZoneInfo(load_config()["defaults"]["timezone"])
            dt = dt.replace(tzinfo=tz)
        unix_ts = int(dt.astimezone(timezone.utc).timestamp())

        fb = for_facebook(metadata.get("caption", ""), metadata.get("hashtags", []))
        caption = fb["caption"]

        video_id = await self._reels_upload(video_path, access_token, page_id)

        # Step 3: finish + schedule
        async with httpx.AsyncClient(timeout=600) as client:
            finish_r = await client.post(
                f"{REELS_API_BASE}/{page_id}/video_reels",
                params={
                    "access_token": access_token,
                    "video_id": video_id,
                    "upload_phase": "finish",
                    "video_state": "SCHEDULED",
                    "scheduled_publish_time": str(unix_ts),
                    "description": caption,
                },
            )
            finish_r.raise_for_status()

        return video_id

    async def get_post_status(self, post_id: str) -> dict[str, Any]:
        token_data = get_auth_token(self.token_key)
        if not token_data:
            return {"status": "not_authenticated"}

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GRAPH_API_BASE}/{post_id}",
                params={
                    "access_token": token_data["access_token"],
                    "fields": "status,title,description,length,published",
                },
            )
            if response.status_code == 200:
                return response.json()
            return {"status": "error", "code": response.status_code}

    async def analyze_timing(self, days: int = 30) -> dict[str, Any]:
        """Analyze best posting times using Page Insights + recent post engagement.

        Returns:
            {
              "fans_online_by_hour": {0..23: avg_count},
              "post_engagement_by_hour": {0..23: {"count": n, "avg_engagement": x}},
              "post_engagement_by_dow":  {0..6:  {"count": n, "avg_engagement": x}},
              "top_posts": [{time, engagement, id, message}],
              "recommendations": {"best_hours": [...], "best_days": [...]}
            }
        """
        from collections import defaultdict
        from zoneinfo import ZoneInfo
        from vidpost.config import load_config

        token_data = get_auth_token(self.token_key)
        if not token_data:
            raise RuntimeError(f"Not authenticated with Facebook ({self.token_key}). Run: vidpost auth facebook")

        page_id = token_data["extra_data"]["page_id"]
        access_token = token_data["access_token"]
        tz = ZoneInfo(load_config()["defaults"]["timezone"])

        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        until = int(datetime.now(timezone.utc).timestamp())

        # page_fans_online was deprecated by Meta in v19+ — no replacement exists.
        fans_by_hour: dict[int, float] = {}
        posts: list[dict[str, Any]] = []

        # v21 modern metrics (post_impressions / post_engaged_users were deprecated)
        api_base = "https://graph.facebook.com/v21.0"
        metrics = "post_impressions_unique,post_reactions_by_type_total,post_clicks,post_video_views"

        async with httpx.AsyncClient(timeout=60) as client:
            url = f"{api_base}/{page_id}/posts"
            params = {
                "access_token": access_token,
                "fields": f"id,message,created_time,insights.metric({metrics})",
                "since": since,
                "until": until,
                "limit": 100,
            }
            while url:
                r = await client.get(url, params=params)
                if r.status_code != 200:
                    break
                body = r.json()
                posts.extend(body.get("data", []))
                url = body.get("paging", {}).get("next")
                params = None
                if len(posts) >= 500:
                    break

        # Aggregate post engagement by local hour / day-of-week
        by_hour: dict[int, list[float]] = defaultdict(list)
        by_dow: dict[int, list[float]] = defaultdict(list)
        flat_posts = []
        for p in posts:
            created = p.get("created_time")
            if not created:
                continue
            try:
                dt_utc = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
            dt_local = dt_utc.astimezone(tz)
            # Composite engagement = reactions (all types) + clicks + video_views
            engaged = 0
            impressions = 0
            for ins in (p.get("insights") or {}).get("data", []):
                name = ins.get("name")
                vals = ins.get("values") or []
                if not vals:
                    continue
                v = vals[0].get("value", 0)
                if name == "post_reactions_by_type_total" and isinstance(v, dict):
                    engaged += sum(int(x or 0) for x in v.values())
                elif name == "post_clicks":
                    engaged += int(v or 0)
                elif name == "post_video_views":
                    engaged += int(v or 0)
                elif name == "post_impressions_unique":
                    impressions = int(v or 0)
            by_hour[dt_local.hour].append(engaged)
            by_dow[dt_local.weekday()].append(engaged)
            flat_posts.append({
                "id": p.get("id"),
                "time": dt_local.isoformat(timespec="minutes"),
                "hour": dt_local.hour,
                "dow": dt_local.weekday(),
                "engagement": engaged,
                "impressions": impressions,
                "message": (p.get("message") or "")[:80],
            })

        def _agg(bucket: dict[int, list[float]]) -> dict[int, dict[str, float]]:
            return {
                k: {"count": len(v), "avg_engagement": round(sum(v) / len(v), 1)}
                for k, v in bucket.items() if v
            }

        hour_stats = _agg(by_hour)
        dow_stats = _agg(by_dow)

        best_hours = sorted(hour_stats.items(), key=lambda kv: kv[1]["avg_engagement"], reverse=True)[:3]
        best_days = sorted(dow_stats.items(), key=lambda kv: kv[1]["avg_engagement"], reverse=True)[:3]
        top_posts = sorted(flat_posts, key=lambda p: p["engagement"], reverse=True)[:5]

        return {
            "fans_online_by_hour": fans_by_hour,
            "post_engagement_by_hour": hour_stats,
            "post_engagement_by_dow": dow_stats,
            "top_posts": top_posts,
            "recommendations": {
                "best_hours": [h for h, _ in best_hours],
                "best_days": [d for d, _ in best_days],
            },
            "sample_size": len(flat_posts),
            "days_analyzed": days,
        }

    async def get_pending_comments(self, days: int = 14, limit_posts: int = 50) -> list[dict[str, Any]]:
        """Return comments on recent posts that the Page hasn't replied to yet.

        Each entry: {comment_id, post_id, post_title, post_caption, author, message, time}
        """
        token_data = get_auth_token(self.token_key)
        if not token_data:
            raise RuntimeError(f"Not authenticated with Facebook ({self.token_key})")
        page_id = token_data["extra_data"]["page_id"]
        access_token = token_data["access_token"]
        api = "https://graph.facebook.com/v21.0"
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

        pending = []
        async with httpx.AsyncClient(timeout=60) as client:
            posts_r = await client.get(f"{api}/{page_id}/posts", params={
                "fields": "id,message,created_time,attachments{title}",
                "since": since, "limit": limit_posts, "access_token": access_token,
            })
            if posts_r.status_code != 200:
                return []
            for p in posts_r.json().get("data", []):
                atts = (p.get("attachments") or {}).get("data", [])
                title = atts[0].get("title", "") if atts else ""
                c_r = await client.get(f"{api}/{p['id']}/comments", params={
                    "filter": "toplevel",
                    "fields": "id,from,message,created_time,comments.limit(10){from,message}",
                    "limit": 100, "access_token": access_token,
                })
                if c_r.status_code != 200:
                    continue
                for c in c_r.json().get("data", []):
                    if c.get("from", {}).get("id") == page_id:
                        continue
                    replies = (c.get("comments") or {}).get("data", [])
                    if any(r.get("from", {}).get("id") == page_id for r in replies):
                        continue
                    if not c.get("message"):
                        continue
                    pending.append({
                        "comment_id": c["id"],
                        "post_id": p["id"],
                        "post_title": title,
                        "post_caption": (p.get("message") or "").strip()[:120],
                        "author": c.get("from", {}).get("name", "(anon)"),
                        "message": c["message"],
                        "time": c["created_time"],
                    })
        return pending

    async def reply_to_comment(self, comment_id: str, message: str) -> dict[str, Any]:
        """Post a reply to a specific comment. Returns {id} on success or {error} on failure."""
        token_data = get_auth_token(self.token_key)
        if not token_data:
            raise RuntimeError(f"Not authenticated with Facebook ({self.token_key})")
        access_token = token_data["access_token"]
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://graph.facebook.com/v21.0/{comment_id}/comments",
                data={"message": message, "access_token": access_token},
            )
            try:
                return r.json()
            except Exception:
                return {"error": {"message": r.text, "code": r.status_code}}

    async def delete_comment(self, comment_id: str) -> bool:
        """Delete a comment (typically one you posted). Returns True on success."""
        token_data = get_auth_token(self.token_key)
        if not token_data:
            raise RuntimeError(f"Not authenticated with Facebook ({self.token_key})")
        access_token = token_data["access_token"]
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(
                f"https://graph.facebook.com/v21.0/{comment_id}",
                params={"access_token": access_token},
            )
            return r.status_code == 200 and r.json().get("success", False)

    async def refresh_token(self) -> bool:
        token_data = get_auth_token(self.token_key)
        if not token_data:
            return False

        config = get_platform_config("facebook")
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GRAPH_API_BASE}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": config.get("app_id", ""),
                    "client_secret": config.get("app_secret", ""),
                    "fb_exchange_token": token_data["access_token"],
                },
            )
            if response.status_code != 200:
                return False
            result = response.json()
            save_auth_token(
                platform=self.token_key,
                access_token=result["access_token"],
                extra_data=token_data.get("extra_data", {}),
            )
            return True
