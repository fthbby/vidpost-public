"""Per-platform caption transformations.

Each platform has different caption/title/description requirements and
conventions. These functions adapt a generic caption to fit each platform.
"""

import re


def _first_meaningful_line(caption: str) -> str:
    """Return the first non-empty line of a caption."""
    for line in caption.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return caption.strip()


def _strip_trailing_hashtags(caption: str) -> str:
    """Remove trailing hashtag-only lines from a caption."""
    lines = caption.rstrip().splitlines()
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
        elif re.fullmatch(r"(#\w+\s*)+", last):
            lines.pop()
        else:
            break
    return "\n".join(lines).rstrip()


def _truncate_at_word(text: str, max_len: int) -> str:
    """Truncate text at the last word boundary within max_len."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    # Back up to the last space/newline to avoid cutting mid-word
    last_break = max(truncated.rfind(" "), truncated.rfind("\n"))
    if last_break > max_len * 0.6:
        truncated = truncated[:last_break]
    return truncated.rstrip() + "…"


def for_tiktok(caption: str, hashtags: list[str] | None = None) -> dict:
    """Prepare caption for TikTok.

    TikTok limits:
    - Title field: 150 characters
    - Total caption: 2200 characters

    Strategy: use the first meaningful line (usually location) as title-ish,
    pack the rest into the description if space allows.
    """
    hashtags = hashtags or []
    text = caption.strip()

    # TikTok combines title + description into the caption. Max 2200 total.
    full = text
    if hashtags:
        tag_str = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
        if tag_str not in full:
            full = f"{full}\n{tag_str}"

    if len(full) > 2200:
        full = _truncate_at_word(full, 2200)

    # Title: first 150 chars at a word boundary, no emojis/newlines ideally
    title_source = _first_meaningful_line(text)
    title = _truncate_at_word(title_source, 150)

    return {"title": title, "caption": full}


def for_youtube(caption: str, hashtags: list[str] | None = None, filename: str = "") -> dict:
    """Prepare caption for YouTube.

    YouTube limits:
    - Title: 100 characters
    - Description: 5000 characters

    Cleanup:
    - Strips @ from IG handles (YT doesn't auto-link them, they look sloppy)
    - Removes em/en dashes (user voice preference)
    - Collapses whitespace from removals
    """
    hashtags = hashtags or []
    description = caption.strip()

    # Strip @ from IG mentions, keep the handle text readable.
    # Lookbehind avoids mangling emails (resvn@ihg.com → resvn.ihg.com).
    description = re.sub(r"(?<!\w)@(\w[\w.]*)", r"\1", description)

    # YouTube rejects descriptions containing < or > with `invalidDescription`.
    description = description.replace("<", "").replace(">", "")

    # Remove em/en dashes (replace with space, collapse runs after)
    description = description.replace("\u2014", " ").replace("\u2013", " ")

    # Collapse whitespace runs caused by replacements
    description = re.sub(r"[ \t]{2,}", " ", description)
    description = re.sub(r" +\n", "\n", description)

    if hashtags:
        tag_str = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
        if tag_str not in description:
            description = f"{description}\n\n{tag_str}"

    if len(description) > 5000:
        description = _truncate_at_word(description, 5000)

    return {"description": description}


def _clean_for_facebook(text: str, max_hashtags: int = 3) -> str:
    """Clean Instagram-style captions for Facebook's algorithm.

    Facebook's algorithm penalizes:
    - Broken @mentions (Instagram handles don't resolve on FB)
    - External URLs (they reduce organic reach significantly)
    - Promotional language (coupon codes, partner tags)
    - Excessive hashtags (FB prefers 1-3 vs IG's 5-30)
    - Cross-posting signals
    """
    # 1. Strip @ symbol from mentions (keeps readability: "@mrsfishla" → "mrsfishla").
    # Lookbehind avoids mangling emails like "name@domain.com".
    text = re.sub(r"(?<!\w)@(\w[\w.]*)", r"\1", text)

    # 2. Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\bwww\.\S+", "", text)

    # Strip <> brackets — Facebook 500s on some bracketed inline content
    # (e.g. <email@x.com>, <PROMOCODE>) and YouTube rejects them outright.
    text = text.replace("<", "").replace(">", "")

    # 3. Remove promotional markers line-by-line
    promo_patterns = [
        re.compile(r"^\s*coupon\s*code\s*[:\-].*$", re.IGNORECASE),
        re.compile(r"^\s*promo\s*code\s*[:\-].*$", re.IGNORECASE),
        re.compile(r"^\s*search\s*id\s*[:\-].*$", re.IGNORECASE),
        re.compile(r"^\s*discount\s*code\s*[:\-].*$", re.IGNORECASE),
        re.compile(r"^-{2,}\s*$"),  # separator lines
    ]
    # Promotional hashtags (ad/partner disclosures) — case insensitive full-tag match
    promo_hashtags = re.compile(r"#\w*(partner|sponsored|ad|paidpartnership)\w*", re.IGNORECASE)

    cleaned_lines = []
    for line in text.splitlines():
        if any(p.match(line) for p in promo_patterns):
            continue
        line = promo_hashtags.sub("", line)
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # 4. Reduce hashtags to max_hashtags — keep the first ones that appear
    all_tags = list(re.finditer(r"#\w+", text))
    if len(all_tags) > max_hashtags:
        # Remove hashtags beyond the limit (from the end backwards)
        keep_tags = {m.start() for m in all_tags[:max_hashtags]}
        result = []
        for m in all_tags:
            if m.start() not in keep_tags:
                text = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]

    # 5. Remove em/en dashes (user voice preference)
    text = text.replace("\u2014", " ").replace("\u2013", " ")

    # 6. Collapse extra whitespace, blank lines, and stray punctuation
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\n +", "\n", text)
    text = re.sub(r" +\n", "\n", text)
    return text.strip()


def for_facebook(caption: str, hashtags: list[str] | None = None, clean: bool = True) -> dict:
    """Prepare caption for Facebook.

    By default, applies algorithm-friendly cleanup:
    - Strips @mentions (IG handles don't work on FB)
    - Removes URLs
    - Removes coupon codes and partner disclosures
    - Reduces hashtags to 3

    Pass clean=False to preserve the original caption verbatim.
    """
    hashtags = hashtags or []
    text = caption.strip()

    if clean:
        text = _clean_for_facebook(text)

    if hashtags:
        tag_str = " ".join(f"#{h.lstrip('#')}" for h in hashtags[:3 if clean else len(hashtags)])
        if tag_str not in text:
            text = f"{text}\n\n{tag_str}"

    return {"caption": text}


def for_instagram(caption: str, hashtags: list[str] | None = None) -> dict:
    """Prepare caption for Instagram.

    Instagram limits:
    - Caption: 2200 characters
    - Max 30 hashtags
    """
    hashtags = (hashtags or [])[:30]
    text = caption.strip()

    if hashtags:
        tag_str = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
        if tag_str not in text:
            text = f"{text}\n\n{tag_str}"

    if len(text) > 2200:
        text = _truncate_at_word(text, 2200)

    return {"caption": text}


def apply(platform: str, caption: str, hashtags: list[str] | None = None, **kwargs) -> dict:
    """Apply per-platform transformations to a caption.

    Returns a dict with platform-appropriate fields (caption, title, description).
    """
    base = platform.split(":")[0].lower()
    if base == "tiktok":
        return for_tiktok(caption, hashtags)
    if base == "youtube":
        return for_youtube(caption, hashtags, filename=kwargs.get("filename", ""))
    if base == "facebook":
        return for_facebook(caption, hashtags)
    if base == "instagram":
        return for_instagram(caption, hashtags)
    return {"caption": caption}
