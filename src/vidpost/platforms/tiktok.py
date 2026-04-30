"""TikTok Content Posting API integration.

Setup:
1. Create an app at https://developers.tiktok.com
2. Apply for Content Posting API access (requires app review)
3. Scopes needed: video.publish, video.upload
4. Store client key/secret in ~/.vidpost/config.yaml

Important: TikTok may require the user to confirm each post in-app
depending on approval level. The tool surfaces this status clearly.
"""

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from vidpost.config import get_platform_config
from vidpost.db import get_auth_token, save_auth_token
from vidpost.platforms.base import PlatformBase

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"


class TikTokPlatform(PlatformBase):
    name = "tiktok"

    async def authenticate(self) -> bool:
        """Run OAuth 2.0 flow for TikTok.

        Opens browser for user authorization, then exchanges code for tokens.
        """
        config = get_platform_config("tiktok")
        client_key = config.get("client_key", "")
        if not client_key:
            raise RuntimeError(
                "TikTok client_key must be set in ~/.vidpost/config.yaml\n"
                "See: https://developers.tiktok.com"
            )

        import webbrowser
        from urllib.parse import urlencode
        import click

        # Generate CSRF token
        csrf_token = hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]

        params = {
            "client_key": client_key,
            "scope": "video.publish,video.upload",
            "response_type": "code",
            "redirect_uri": "http://localhost:8585/callback",
            "state": csrf_token,
        }

        auth_url = f"{TIKTOK_AUTH_URL}?{urlencode(params)}"
        click.echo(f"Opening browser for TikTok authorization...")
        click.echo(f"If browser doesn't open, visit:\n{auth_url}")
        webbrowser.open(auth_url)

        # Simple callback server to capture the auth code
        auth_code = click.prompt("Paste the authorization code from the redirect URL")

        # Exchange code for tokens
        async with httpx.AsyncClient() as client:
            response = await client.post(
                TIKTOK_TOKEN_URL,
                data={
                    "client_key": client_key,
                    "client_secret": config.get("client_secret", ""),
                    "code": auth_code,
                    "grant_type": "authorization_code",
                    "redirect_uri": "http://localhost:8585/callback",
                },
            )
            response.raise_for_status()
            result = response.json()

        token_data = result.get("data", result)
        expires_in = token_data.get("expires_in", 86400)
        expires_at = datetime.now(timezone.utc).timestamp() + expires_in

        save_auth_token(
            platform="tiktok",
            access_token=token_data.get("access_token", ""),
            refresh_token=token_data.get("refresh_token"),
            expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
            extra_data={"open_id": token_data.get("open_id", "")},
        )
        return True

    async def upload_video(self, video_path: Path, metadata: dict[str, Any]) -> str:
        """Upload video to TikTok inbox.

        TikTok uses a two-step process:
        1. Initialize upload → get upload URL
        2. Upload video chunks to that URL
        Video goes to user's TikTok inbox for final confirmation.
        """
        token_data = get_auth_token("tiktok")
        if not token_data:
            raise RuntimeError("Not authenticated with TikTok. Run: vidpost auth tiktok")

        from vidpost.captions.transforms import for_tiktok

        file_size = video_path.stat().st_size

        tt = for_tiktok(metadata.get("caption", ""), metadata.get("hashtags", []))
        title = tt["title"]

        # Step 1: Initialize upload
        async with httpx.AsyncClient(timeout=600) as client:
            init_response = await client.post(
                TIKTOK_PUBLISH_URL,
                headers={
                    "Authorization": f"Bearer {token_data['access_token']}",
                    "Content-Type": "application/json",
                },
                json={
                    "post_info": {
                        "title": title,
                        "privacy_level": "SELF_ONLY",  # User can change in app
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": file_size,
                        "chunk_size": file_size,  # Single chunk for simplicity
                        "total_chunk_count": 1,
                    },
                },
            )
            init_response.raise_for_status()
            init_data = init_response.json().get("data", {})
            publish_id = init_data.get("publish_id", "")
            upload_url = init_data.get("upload_url", "")

            if not upload_url:
                raise RuntimeError(f"TikTok didn't return an upload URL. Response: {init_response.json()}")

            # Step 2: Upload the video file
            with open(video_path, "rb") as f:
                upload_response = await client.put(
                    upload_url,
                    content=f.read(),
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                    },
                )
                upload_response.raise_for_status()

        return publish_id

    async def schedule_video(self, video_path: Path, metadata: dict[str, Any], publish_at: str) -> str:
        """TikTok doesn't natively support scheduled publishing via API.
        Upload to inbox and note the intended publish time."""
        publish_id = await self.upload_video(video_path, metadata)
        return publish_id

    async def get_post_status(self, post_id: str) -> dict[str, Any]:
        token_data = get_auth_token("tiktok")
        if not token_data:
            return {"status": "not_authenticated"}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
                headers={
                    "Authorization": f"Bearer {token_data['access_token']}",
                    "Content-Type": "application/json",
                },
                json={"publish_id": post_id},
            )
            if response.status_code == 200:
                return response.json().get("data", {})
            return {"status": "error", "code": response.status_code}

    async def refresh_token(self) -> bool:
        token_data = get_auth_token("tiktok")
        if not token_data or not token_data.get("refresh_token"):
            return False

        config = get_platform_config("tiktok")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                TIKTOK_TOKEN_URL,
                data={
                    "client_key": config.get("client_key", ""),
                    "client_secret": config.get("client_secret", ""),
                    "grant_type": "refresh_token",
                    "refresh_token": token_data["refresh_token"],
                },
            )
            if response.status_code != 200:
                return False
            result = response.json()
            data = result.get("data", result)
            expires_in = data.get("expires_in", 86400)

            save_auth_token(
                platform="tiktok",
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token"),
                expires_at=datetime.fromtimestamp(
                    datetime.now(timezone.utc).timestamp() + expires_in, tz=timezone.utc
                ),
                extra_data=token_data.get("extra_data", {}),
            )
            return True
