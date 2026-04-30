"""Abstract base class for platform integrations."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class PlatformBase(ABC):
    """Base class all platform uploaders must implement."""

    name: str = "base"

    @abstractmethod
    async def authenticate(self) -> bool:
        """Run OAuth flow or validate existing credentials. Returns True if authenticated."""
        ...

    @abstractmethod
    async def upload_video(self, video_path: Path, metadata: dict[str, Any]) -> str:
        """Upload a video immediately. Returns platform post ID."""
        ...

    @abstractmethod
    async def schedule_video(self, video_path: Path, metadata: dict[str, Any], publish_at: str) -> str:
        """Schedule a video for future publishing. Returns platform post ID."""
        ...

    @abstractmethod
    async def get_post_status(self, post_id: str) -> dict[str, Any]:
        """Get status of a post by its platform ID."""
        ...

    @abstractmethod
    async def refresh_token(self) -> bool:
        """Refresh OAuth token. Returns True if successful."""
        ...

    def is_authenticated(self) -> bool:
        """Check if we have valid stored credentials."""
        from vidpost.db import get_auth_token
        token = get_auth_token(self.name)
        return token is not None and bool(token.get("access_token"))
