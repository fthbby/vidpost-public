"""YouTube Data API v3 integration.

Setup:
1. Create a Google Cloud project at https://console.cloud.google.com
2. Enable "YouTube Data API v3"
3. Create OAuth 2.0 credentials (Desktop app type)
4. Download client_secret.json → ~/.vidpost/youtube_client_secret.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vidpost.config import get_platform_config
from vidpost.db import get_auth_token, save_auth_token
from vidpost.platforms.base import PlatformBase



def _title_from_filename(stem: str) -> str:
    """Generate a clean title from a video filename stem.

    Strips leading date prefixes like '2026-03-29_' and converts
    hyphens/underscores to spaces.
    """
    import re
    # Strip leading YYYY-MM-DD_ prefix
    stem = re.sub(r'^\d{4}-\d{2}-\d{2}_', '', stem)
    return stem.replace("-", " ").replace("_", " ").title()


class YouTubePlatform(PlatformBase):
    name = "youtube"

    async def authenticate(self) -> bool:
        """Run OAuth 2.0 flow for YouTube using Google's libraries."""
        config = get_platform_config("youtube")
        secret_path = Path(config.get("client_secret_path", "")).expanduser()

        if not secret_path.exists():
            raise FileNotFoundError(
                f"YouTube client secret not found at {secret_path}\n"
                "Download it from Google Cloud Console → APIs & Services → Credentials\n"
                "See: https://console.cloud.google.com"
            )

        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            raise ImportError("Install google-auth-oauthlib: pip install google-auth-oauthlib")

        scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ]
        flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), scopes)
        credentials = flow.run_local_server(
            port=8089,
            open_browser=False,
            prompt_message="Open this URL in Safari to authorize:\n\n{url}\n",
            success_message="Authorization complete! You can close this tab.",
        )

        # Fetch and cache channel_id so get_pending_comments can detect self-replies
        channel_id = ""
        try:
            from googleapiclient.discovery import build
            svc = build("youtube", "v3", credentials=credentials)
            resp = svc.channels().list(part="id", mine=True).execute()
            items = resp.get("items", [])
            if items:
                channel_id = items[0]["id"]
        except Exception:
            pass

        save_auth_token(
            platform="youtube",
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            expires_at=credentials.expiry,
            extra_data={"client_secret_path": str(secret_path), "channel_id": channel_id},
        )
        return True

    async def upload_video(self, video_path: Path, metadata: dict[str, Any]) -> str:
        """Upload video to YouTube using Data API v3."""
        from vidpost.captions.transforms import for_youtube

        service = self._get_service()

        yt = for_youtube(metadata.get("caption", ""), metadata.get("hashtags", []))
        title = metadata.get("title", _title_from_filename(video_path.stem))[:100]
        description = metadata.get("description", yt["description"])

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": metadata.get("hashtags", []),
                "categoryId": str(metadata.get("category", 22)),
            },
            "status": {
                "privacyStatus": metadata.get("privacy", get_platform_config("youtube").get("default_privacy", "unlisted")),
            },
        }

        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            raise ImportError("Install google-api-python-client: pip install google-api-python-client")

        media = MediaFileUpload(str(video_path), resumable=True, chunksize=10 * 1024 * 1024)
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = request.next_chunk()

        return response["id"]

    async def schedule_video(self, video_path: Path, metadata: dict[str, Any], publish_at: str) -> str:
        """Schedule video on YouTube using publishAt."""
        from vidpost.captions.transforms import for_youtube

        service = self._get_service()

        # Convert to ISO format with timezone
        dt = datetime.fromisoformat(publish_at)
        if dt.tzinfo is None:
            from zoneinfo import ZoneInfo
            from vidpost.config import load_config
            tz = ZoneInfo(load_config()["defaults"]["timezone"])
            dt = dt.replace(tzinfo=tz)
        publish_at_utc = dt.astimezone(timezone.utc).isoformat()

        yt = for_youtube(metadata.get("caption", ""), metadata.get("hashtags", []))
        title = metadata.get("title", _title_from_filename(video_path.stem))[:100]
        description = metadata.get("description", yt["description"])

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": metadata.get("hashtags", []),
                "categoryId": str(metadata.get("category", 22)),
            },
            "status": {
                "privacyStatus": "private",
                "publishAt": publish_at_utc,
            },
        }

        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            raise ImportError("Install google-api-python-client: pip install google-api-python-client")

        media = MediaFileUpload(str(video_path), resumable=True, chunksize=10 * 1024 * 1024)
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = request.next_chunk()

        return response["id"]

    async def get_post_status(self, post_id: str) -> dict[str, Any]:
        service = self._get_service()
        response = service.videos().list(part="status,snippet", id=post_id).execute()
        if not response.get("items"):
            return {"status": "not_found"}
        item = response["items"][0]
        return {
            "status": item["status"]["uploadStatus"],
            "privacy": item["status"]["privacyStatus"],
            "title": item["snippet"]["title"],
            "url": f"https://youtube.com/watch?v={post_id}",
        }

    async def refresh_token(self) -> bool:
        token_data = get_auth_token("youtube")
        if not token_data or not token_data.get("refresh_token"):
            return False

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError:
            return False

        extra = token_data.get("extra_data", {})
        secret_path = extra.get("client_secret_path")
        if not secret_path or not Path(secret_path).exists():
            return False

        with open(secret_path) as f:
            client_config = json.load(f)
        installed = client_config.get("installed", client_config.get("web", {}))

        creds = Credentials(
            token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=installed["client_id"],
            client_secret=installed["client_secret"],
        )
        creds.refresh(Request())

        save_auth_token(
            platform="youtube",
            access_token=creds.token,
            refresh_token=creds.refresh_token,
            expires_at=creds.expiry,
            extra_data=extra,
        )
        return True

    async def get_pending_comments(self, days: int = 14, limit_posts: int = 50) -> list[dict[str, Any]]:
        """Return top-level comments on recent uploads that you haven't replied to.

        Each entry: {comment_id, post_id, post_title, post_caption, author, message, time}
        """
        from datetime import timedelta

        service = self._get_service()

        token_data = get_auth_token("youtube") or {}
        extra = token_data.get("extra_data", {})
        channel_id = extra.get("channel_id", "")
        if not channel_id:
            # Lazy backfill: older tokens may not have channel_id cached
            try:
                me = service.channels().list(part="id", mine=True).execute()
                items = me.get("items", [])
                if items:
                    channel_id = items[0]["id"]
                    extra["channel_id"] = channel_id
                    save_auth_token(
                        platform="youtube",
                        access_token=token_data["access_token"],
                        refresh_token=token_data.get("refresh_token"),
                        expires_at=token_data.get("expires_at"),
                        extra_data=extra,
                    )
            except Exception:
                pass

        # Find the uploads playlist for this channel
        ch_resp = service.channels().list(part="contentDetails", mine=True).execute()
        ch_items = ch_resp.get("items", [])
        if not ch_items:
            return []
        uploads_playlist = ch_items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Collect recent video IDs
        videos: list[dict[str, str]] = []
        page_token = None
        while True:
            pl = service.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=uploads_playlist,
                maxResults=50,
                pageToken=page_token,
            ).execute()
            stop = False
            for it in pl.get("items", []):
                published = it["contentDetails"].get("videoPublishedAt") or it["snippet"].get("publishedAt")
                if not published:
                    continue
                try:
                    dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if dt < cutoff:
                    stop = True
                    break
                videos.append({
                    "id": it["contentDetails"]["videoId"],
                    "title": it["snippet"].get("title", ""),
                    "description": it["snippet"].get("description", ""),
                })
                if len(videos) >= limit_posts:
                    stop = True
                    break
            if stop:
                break
            page_token = pl.get("nextPageToken")
            if not page_token:
                break

        pending: list[dict[str, Any]] = []
        for v in videos:
            ct_page = None
            while True:
                try:
                    ct = service.commentThreads().list(
                        part="snippet,replies",
                        videoId=v["id"],
                        maxResults=100,
                        textFormat="plainText",
                        pageToken=ct_page,
                    ).execute()
                except Exception:
                    # Comments disabled or other error on this video — skip
                    break
                for thread in ct.get("items", []):
                    top = thread["snippet"]["topLevelComment"]
                    top_snippet = top["snippet"]
                    author_channel = (top_snippet.get("authorChannelId") or {}).get("value", "")
                    # Skip comments posted by the channel owner themselves
                    if channel_id and author_channel == channel_id:
                        continue
                    # Skip if any reply is from channel owner
                    replies = (thread.get("replies") or {}).get("comments", [])
                    already_replied = False
                    for r in replies:
                        r_author = (r["snippet"].get("authorChannelId") or {}).get("value", "")
                        if channel_id and r_author == channel_id:
                            already_replied = True
                            break
                    if already_replied:
                        continue
                    pending.append({
                        "comment_id": top["id"],
                        "post_id": v["id"],
                        "post_title": v["title"],
                        "post_caption": (v["description"] or "").strip()[:120],
                        "author": top_snippet.get("authorDisplayName", "(anon)"),
                        "message": top_snippet.get("textDisplay", ""),
                        "time": top_snippet.get("publishedAt", ""),
                    })
                ct_page = ct.get("nextPageToken")
                if not ct_page:
                    break
        return pending

    async def reply_to_comment(self, comment_id: str, message: str) -> dict[str, Any]:
        """Post a reply to a top-level comment. Returns {id} or {error}."""
        service = self._get_service()
        try:
            resp = service.comments().insert(
                part="snippet",
                body={"snippet": {"parentId": comment_id, "textOriginal": message}},
            ).execute()
            return {"id": resp.get("id", "")}
        except Exception as e:
            # googleapiclient.errors.HttpError has content; fall back to str
            msg = getattr(e, "reason", None) or str(e)
            return {"error": {"message": msg}}

    async def delete_comment(self, comment_id: str) -> bool:
        """Delete a comment (your own). Returns True on success."""
        service = self._get_service()
        try:
            service.comments().delete(id=comment_id).execute()
            return True
        except Exception:
            return False

    def _get_service(self):
        """Build an authenticated YouTube API service."""
        token_data = get_auth_token("youtube")
        if not token_data:
            raise RuntimeError("Not authenticated with YouTube. Run: vidpost auth youtube")

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            raise ImportError("Install google-api-python-client google-auth: pip install google-api-python-client google-auth")

        extra = token_data.get("extra_data", {})
        secret_path = extra.get("client_secret_path")
        client_id = client_secret = None
        if secret_path and Path(secret_path).exists():
            with open(secret_path) as f:
                client_config = json.load(f)
            installed = client_config.get("installed", client_config.get("web", {}))
            client_id = installed.get("client_id")
            client_secret = installed.get("client_secret")

        creds = Credentials(
            token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
        )
        return build("youtube", "v3", credentials=creds)
