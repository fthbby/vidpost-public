"""Platform integrations for vidpost."""

from vidpost.platforms.base import PlatformBase
from vidpost.platforms.youtube import YouTubePlatform
from vidpost.platforms.facebook import FacebookPlatform
from vidpost.platforms.tiktok import TikTokPlatform

PLATFORMS = {
    "youtube": YouTubePlatform,
    "facebook": FacebookPlatform,
    "tiktok": TikTokPlatform,
}


def get_platform(name: str, **kwargs) -> PlatformBase:
    """Get a platform instance. For facebook, pass page='secondary' to target a specific page."""
    # Handle facebook:pagename syntax
    if ":" in name:
        platform_name, page = name.split(":", 1)
        platform_name = platform_name.lower()
        kwargs["page"] = page
    else:
        platform_name = name.lower()

    cls = PLATFORMS.get(platform_name)
    if not cls:
        raise ValueError(f"Unknown platform: {name}. Available: {', '.join(PLATFORMS)}")

    # Only FacebookPlatform accepts page kwarg
    if platform_name == "facebook":
        return cls(page=kwargs.get("page"))
    return cls()
