"""Data models for vidpost."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class PostStatus(str, Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    UPLOADING = "uploading"
    POSTED = "posted"
    FAILED = "failed"


class Platform(str, Enum):
    YOUTUBE = "youtube"
    FACEBOOK = "facebook"
    TIKTOK = "tiktok"

    @classmethod
    def from_str(cls, s: str) -> "Platform":
        return cls(s.lower().strip())

    @classmethod
    def parse_list(cls, s: str) -> list["Platform"]:
        return [cls.from_str(p) for p in s.split(",") if p.strip()]


@dataclass
class PostRecord:
    id: str
    video_path: str
    platform: Platform
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    status: PostStatus = PostStatus.PENDING
    scheduled_at: datetime | None = None
    posted_at: datetime | None = None
    platform_post_id: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    metadata_path: str | None = None


@dataclass
class VideoMetadata:
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    title: str = ""  # Optional YT title (from TITLE: line in captions.txt)
    platforms: list[str] = field(default_factory=list)
    schedule: str | None = None
    platform_overrides: dict[str, dict] = field(default_factory=dict)

    @property
    def full_caption(self) -> str:
        if not self.hashtags:
            return self.caption
        tags = " ".join(f"#{h.lstrip('#')}" for h in self.hashtags)
        return f"{self.caption}\n{tags}"


@dataclass
class CaptionAnalysis:
    """Output from video analysis pipeline — transcript + keyframe paths."""

    video_path: str
    transcript: str | None = None
    keyframe_paths: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    context: str = ""
