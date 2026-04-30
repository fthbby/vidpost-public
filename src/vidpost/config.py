"""Config management for vidpost. Reads/writes ~/.vidpost/config.yaml."""

from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path.home() / ".vidpost"
CONFIG_PATH = CONFIG_DIR / "config.yaml"
STYLE_GUIDE_PATH = CONFIG_DIR / "style_guide.yaml"
LOG_DIR = CONFIG_DIR / "logs"

DEFAULT_CONFIG = {
    "youtube": {
        "client_secret_path": str(CONFIG_DIR / "youtube_client_secret.json"),
        "default_privacy": "unlisted",
        "default_category": 22,
    },
    "facebook": {
        "app_id": "",
        "app_secret": "",
        "page_id": "",
    },
    "tiktok": {
        "client_key": "",
        "client_secret": "",
    },
    "captions": {
        "whisper_model": "base",
        "num_keyframes": 5,
        "num_options": 3,
        "style_guide_path": str(STYLE_GUIDE_PATH),
    },
    "defaults": {
        "platforms": ["youtube", "facebook", "tiktok"],
        "timezone": "America/Los_Angeles",
        "hashtag_style": "inline",
    },
}

DEFAULT_STYLE_GUIDE = {
    "style": {
        "tone": "casual, conversational",
        "format": "optional location/context on top line, caption body, hashtags at bottom",
        "emoji_usage": "moderate — 1-3 per caption",
        "length": "short and punchy, 2-4 sentences max",
        "hashtag_count": 5,
    },
    "examples": [
        {
            "context": "Replace this with a real example from your own posts",
            "caption": (
                "📍 Optional location line\n"
                "Your caption body — keep it short and in your own voice.\n"
                "#tag1 #tag2 #tag3 #tag4 #tag5"
            ),
        },
    ],
    "platform_style": {
        "youtube": {
            "title_style": "Descriptive, slightly clickbaity but authentic",
            "description_style": "Longer than TikTok/FB caption. Include any relevant context.",
        },
        "tiktok": {
            "adjustments": "More hype, more emoji, more direct. Shorter than other platforms.",
        },
        "facebook": {
            "adjustments": "Same as base style, slightly more descriptive.",
        },
    },
}


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_PATH) as f:
        user_config = yaml.safe_load(f) or {}
    merged = DEFAULT_CONFIG.copy()
    for key, value in user_config.items():
        if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def save_config(config: dict[str, Any]) -> None:
    ensure_config_dir()
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def load_style_guide() -> dict[str, Any]:
    path = Path(load_config()["captions"].get("style_guide_path", STYLE_GUIDE_PATH))
    if not path.exists():
        return DEFAULT_STYLE_GUIDE.copy()
    with open(path) as f:
        return yaml.safe_load(f) or DEFAULT_STYLE_GUIDE.copy()


def save_style_guide(guide: dict[str, Any], path: Path | None = None) -> None:
    path = path or STYLE_GUIDE_PATH
    ensure_config_dir()
    with open(path, "w") as f:
        yaml.dump(guide, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def get_platform_config(platform: str) -> dict[str, Any]:
    config = load_config()
    return config.get(platform, {})
