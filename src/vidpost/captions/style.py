"""Style guide loader — builds context for caption generation."""

from vidpost.config import load_style_guide


def format_style_context() -> str:
    """Load style guide and format it as context for caption generation.

    This output is designed to be read by Claude Code when the user asks
    for caption generation — it provides the voice/tone reference.
    """
    guide = load_style_guide()
    style = guide.get("style", {})
    examples = guide.get("examples", [])
    platform_style = guide.get("platform_style", {})

    parts = ["## Your Caption Style Guide\n"]

    # Style preferences
    if style:
        parts.append("**Style:**")
        for key, value in style.items():
            parts.append(f"- {key.replace('_', ' ').title()}: {value}")
        parts.append("")

    # Example captions
    if examples:
        parts.append(f"**Example Captions ({len(examples)} examples):**\n")
        for i, ex in enumerate(examples, 1):
            parts.append(f"Example {i} — {ex.get('context', 'No context')}:")
            parts.append(f"```\n{ex.get('caption', '').strip()}\n```\n")

    # Platform adjustments
    if platform_style:
        parts.append("**Platform Adjustments:**")
        for platform, adjustments in platform_style.items():
            parts.append(f"\n*{platform.title()}:*")
            for key, value in adjustments.items():
                parts.append(f"- {key.replace('_', ' ').title()}: {value}")

    return "\n".join(parts)


def format_analysis_context(transcript: str | None, keyframe_paths: list[str], duration: float, extra_context: str = "") -> str:
    """Format video analysis results as context for caption generation."""
    parts = ["## Video Analysis\n"]

    parts.append(f"**Duration:** {duration:.0f} seconds ({duration/60:.1f} minutes)\n")

    if transcript:
        parts.append("**Audio Transcript:**")
        parts.append(f"```\n{transcript}\n```\n")
    else:
        parts.append("**Audio:** No speech detected (b-roll / music only)\n")

    if keyframe_paths:
        parts.append(f"**Keyframes:** {len(keyframe_paths)} frames extracted")
        parts.append("(View these images for visual context about the video content)\n")
        for path in keyframe_paths:
            parts.append(f"- `{path}`")
        parts.append("")

    if extra_context:
        parts.append(f"**Additional Context:** {extra_context}\n")

    return "\n".join(parts)
