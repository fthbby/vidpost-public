"""Video metadata: caption.txt lookup files + YAML sidecar fallback.

Caption file format (caption.txt in same folder as videos):

    first-video.mp4
    📍 Optional location line
    Your caption body goes here
    #hashtag1 #hashtag2 #hashtag3

    second-video.mp4
    Another caption body
    #more #tags

Each entry: filename on its own line, followed by caption lines,
separated by a blank line from the next entry.
"""

from pathlib import Path

import yaml

from vidpost.models import VideoMetadata

CAPTION_FILENAMES = ("caption.txt", "captions.txt")


def find_caption_file(video_path: str | Path) -> Path | None:
    """Find a caption.txt file in the same directory as the video."""
    folder = Path(video_path).parent
    for name in CAPTION_FILENAMES:
        path = folder / name
        if path.exists():
            return path
    return None


def parse_caption_file(caption_path: Path) -> dict[str, str]:
    """Parse a caption.txt file into {filename: caption_text} mapping.

    Backwards-compat: returns the raw caption block per filename. Use
    parse_caption_file_rich() to also get title + hashtags split out.
    """
    return {k: v["caption_raw"] for k, v in parse_caption_file_rich(caption_path).items()}


def parse_caption_file_rich(caption_path: Path) -> dict[str, dict]:
    """Parse a caption.txt file into {filename: {title, caption, hashtags, caption_raw}}.

    Format per entry:

        [filename.mp4]
        TITLE: optional YouTube title
        caption body line 1
        caption body line 2
        #trailing #hashtags #become #a #list

    - First line after the [filename] header may start with "TITLE:" — stripped
      off and stored separately.
    - Trailing hashtag-only lines get split into `hashtags: list[str]`.
    - Everything in between is the caption.
    - `caption_raw` preserves the entire block untouched for backwards compat.
    """
    import re

    video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    text = caption_path.read_text(encoding="utf-8").strip()
    if not text:
        return {}

    entries: dict[str, dict] = {}
    current_filename: str | None = None
    current_lines: list[str] = []

    def _flush():
        nonlocal current_filename, current_lines
        if current_filename:
            raw = "\n".join(current_lines).strip()
            title, body, tags = _split_caption_block(raw)
            entries[current_filename] = {
                "title": title,
                "caption": body,
                "hashtags": tags,
                "caption_raw": raw,
            }
        current_filename = None
        current_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        bracket_match = re.match(r'^\[(.+)\]$', stripped)
        if bracket_match:
            candidate = bracket_match.group(1)
            if Path(candidate).suffix.lower() in video_extensions:
                _flush()
                current_filename = candidate
                continue
        if stripped and Path(stripped).suffix.lower() in video_extensions:
            _flush()
            current_filename = stripped
            continue
        if current_filename is not None:
            current_lines.append(line.rstrip())

    _flush()
    return entries


def _split_caption_block(block: str) -> tuple[str, str, list[str]]:
    """Extract (title, caption, hashtags) from one entry's raw text block."""
    import re

    if not block.strip():
        return "", "", []

    lines = block.splitlines()

    # Pull out TITLE: line if it's the first non-empty line
    title = ""
    start = 0
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        m = re.match(r'^\s*TITLE\s*:\s*(.+?)\s*$', ln)
        if m:
            title = m.group(1).strip()
            start = i + 1
        break

    body_lines = lines[start:]

    # Pull trailing hashtag-only lines into a list
    hashtags: list[str] = []
    while body_lines:
        last = body_lines[-1].strip()
        if not last:
            body_lines.pop()
            continue
        if re.fullmatch(r'(#\w+\s*)+', last):
            found = re.findall(r'#(\w+)', last)
            hashtags = found + hashtags  # preserve order across multiple tag lines
            body_lines.pop()
            continue
        break

    caption = "\n".join(body_lines).strip()
    return title, caption, hashtags


def load_caption_for_video(video_path: str | Path) -> str | None:
    """Look up a video's caption block from caption.txt (raw, backwards-compat)."""
    entry = load_caption_entry(video_path)
    return entry["caption_raw"] if entry else None


def load_caption_entry(video_path: str | Path) -> dict | None:
    """Look up a video's rich entry {title, caption, hashtags, caption_raw}."""
    video_path = Path(video_path)
    caption_file = find_caption_file(video_path)
    if not caption_file:
        return None

    entries = parse_caption_file_rich(caption_file)
    if video_path.name in entries:
        return entries[video_path.name]
    lower_name = video_path.name.lower()
    for fname, entry in entries.items():
        if fname.lower() == lower_name:
            return entry
    return None


def sidecar_path(video_path: str | Path) -> Path:
    return Path(video_path).with_suffix(".yaml")


def load_metadata(video_path: str | Path) -> VideoMetadata | None:
    """Load video metadata. Priority: caption.txt > .yaml sidecar.

    captions.txt now supports a TITLE: line (optional, first line after the
    filename header) and trailing #hashtag lines, which are split into
    VideoMetadata.title and .hashtags respectively.
    """
    video_path = Path(video_path)

    entry = load_caption_entry(video_path)

    yaml_path = sidecar_path(video_path)
    yaml_meta = None
    if yaml_path.exists():
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
        yaml_meta = VideoMetadata(
            caption=data.get("caption", ""),
            hashtags=data.get("hashtags", []),
            title=data.get("title", ""),
            platforms=data.get("platforms", []),
            schedule=data.get("schedule"),
            platform_overrides=data.get("platform_overrides", {}),
        )

    if entry and yaml_meta:
        # captions.txt wins for caption/title/hashtags; YAML provides platforms/schedule/overrides
        yaml_meta.caption = entry["caption"]
        if entry["title"]:
            yaml_meta.title = entry["title"]
        if entry["hashtags"]:
            yaml_meta.hashtags = entry["hashtags"]
        return yaml_meta
    elif entry:
        return VideoMetadata(
            caption=entry["caption"],
            title=entry["title"],
            hashtags=entry["hashtags"],
        )
    elif yaml_meta:
        return yaml_meta
    return None


def save_metadata(video_path: str | Path, meta: VideoMetadata) -> Path:
    path = sidecar_path(video_path)
    data: dict = {}
    if meta.caption:
        data["caption"] = meta.caption
    if meta.hashtags:
        data["hashtags"] = meta.hashtags
    if meta.platforms:
        data["platforms"] = meta.platforms
    if meta.schedule:
        data["schedule"] = meta.schedule
    if meta.platform_overrides:
        data["platform_overrides"] = meta.platform_overrides
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return path


def find_videos(folder: str | Path, extensions: tuple[str, ...] = (".mp4", ".mov", ".avi", ".mkv", ".webm")) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    videos = []
    for ext in extensions:
        videos.extend(folder.glob(f"*{ext}"))
    return sorted(videos)
