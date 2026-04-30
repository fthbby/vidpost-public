"""Main CLI entry point for vidpost. Click-based with subcommands."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from vidpost.config import (
    CONFIG_DIR, CONFIG_PATH, STYLE_GUIDE_PATH,
    ensure_config_dir, load_config, save_config, save_style_guide,
    DEFAULT_CONFIG, DEFAULT_STYLE_GUIDE,
)
from vidpost.db import (
    init_db, create_post, get_post, get_posts, get_scheduled_posts,
    update_post_status, delete_post, get_posted_targets,
)
from vidpost.metadata import find_videos, load_metadata, load_caption_for_video, find_caption_file, save_metadata, sidecar_path
from vidpost.models import Platform, PostStatus, VideoMetadata

console = Console()


def _get_timezone() -> ZoneInfo:
    config = load_config()
    return ZoneInfo(config["defaults"]["timezone"])


def _parse_schedule(schedule_str: str) -> datetime:
    """Parse a schedule string into a timezone-aware datetime."""
    tz = _get_timezone()
    try:
        dt = datetime.fromisoformat(schedule_str)
    except ValueError:
        # Try common formats
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M"):
            try:
                dt = datetime.strptime(schedule_str, fmt)
                break
            except ValueError:
                continue
        else:
            raise click.BadParameter(f"Cannot parse schedule time: {schedule_str}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """vidpost — Multi-platform video posting tool."""
    init_db()


# ── vidpost init ──────────────────────────────────────────────

@cli.command()
@click.option("--platform", type=click.Choice(["youtube", "facebook", "tiktok"]), help="Set up a specific platform only")
def init(platform):
    """Initialize vidpost config and run setup wizard."""
    ensure_config_dir()

    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        console.print(f"[green]Created config:[/green] {CONFIG_PATH}")
    else:
        console.print(f"[dim]Config exists:[/dim] {CONFIG_PATH}")

    if not STYLE_GUIDE_PATH.exists():
        save_style_guide(DEFAULT_STYLE_GUIDE)
        console.print(f"[green]Created style guide:[/green] {STYLE_GUIDE_PATH}")
    else:
        console.print(f"[dim]Style guide exists:[/dim] {STYLE_GUIDE_PATH}")

    console.print()
    console.print(Panel.fit(
        "[bold]Next steps:[/bold]\n\n"
        "1. Edit [cyan]~/.vidpost/config.yaml[/cyan] with your platform credentials\n"
        "2. Edit [cyan]~/.vidpost/style_guide.yaml[/cyan] with your caption examples\n"
        "3. Run [cyan]vidpost auth <platform>[/cyan] to authenticate\n"
        "4. Run [cyan]vidpost caption <video>[/cyan] to analyze a video",
        title="vidpost setup complete",
    ))


# ── vidpost auth ──────────────────────────────────────────────

@cli.command()
@click.argument("platform", type=click.Choice(["youtube", "facebook", "tiktok"]))
def auth(platform):
    """Authenticate with a platform."""
    from vidpost.platforms import get_platform

    p = get_platform(platform)
    console.print(f"Authenticating with [bold]{platform}[/bold]...")

    try:
        result = asyncio.run(p.authenticate())
        if result:
            console.print(f"[green]Successfully authenticated with {platform}![/green]")
        else:
            console.print(f"[red]Authentication failed for {platform}[/red]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")


# ── vidpost caption ───────────────────────────────────────────

@cli.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--context", "-c", default="", help="Extra context about the video")
@click.option("--no-audio", is_flag=True, help="Skip audio transcription")
@click.option("--frames", "-f", type=int, default=None, help="Number of keyframes to extract")
def caption(path, context, no_audio, frames):
    """Analyze a video and prepare context for caption generation.

    Extracts audio transcript (via Whisper) and keyframes, then outputs
    everything you need to generate captions with Claude Code.
    """
    from vidpost.captions.analyzer import analyze_video
    from vidpost.captions.style import format_style_context, format_analysis_context

    path = Path(path)

    if path.is_dir():
        videos = find_videos(path)
        if not videos:
            console.print(f"[red]No video files found in {path}[/red]")
            return
        console.print(f"Found [bold]{len(videos)}[/bold] videos in {path}\n")
        for video in videos:
            _analyze_single_video(video, context, no_audio, frames)
            console.print("---\n")
    else:
        _analyze_single_video(path, context, no_audio, frames)


def _analyze_single_video(video_path: Path, context: str, no_audio: bool, frames: int | None):
    """Analyze a single video and print results."""
    from vidpost.captions.analyzer import analyze_video
    from vidpost.captions.style import format_style_context, format_analysis_context

    console.print(f"Analyzing [bold]{video_path.name}[/bold]...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Extracting audio and keyframes...", total=None)
        analysis = analyze_video(
            video_path,
            num_keyframes=frames,
            skip_audio=no_audio,
            context=context,
        )
        progress.update(task, description="Done!", completed=True)

    # Print analysis results
    console.print()
    console.print(Panel.fit(
        format_analysis_context(
            analysis.transcript,
            analysis.keyframe_paths,
            analysis.duration_seconds,
            analysis.context,
        ),
        title=f"Analysis: {video_path.name}",
    ))

    # Print style guide for reference
    console.print()
    console.print(Panel.fit(
        format_style_context(),
        title="Your Style Guide",
    ))

    # Check for existing caption
    existing = load_metadata(video_path)
    if existing and existing.caption:
        cap_file = find_caption_file(video_path)
        source = cap_file if cap_file else sidecar_path(video_path)
        console.print(f"\n[dim]Existing caption from: {source}[/dim]")
        console.print(f"[dim]{existing.caption[:100]}[/dim]")
    else:
        folder = video_path.parent
        console.print(
            f"\n[yellow]No caption found.[/yellow] Add an entry for [cyan]{video_path.name}[/cyan] in:\n"
            f"  [cyan]{folder / 'caption.txt'}[/cyan]\n"
            f"Or ask Claude Code to generate one based on the analysis above."
        )

    # Print keyframe paths for Claude Code to view
    if analysis.keyframe_paths:
        console.print(f"\n[dim]Keyframes saved to view:[/dim]")
        for kf in analysis.keyframe_paths:
            console.print(f"  {kf}")


# ── vidpost set-caption ───────────────────────────────────────

@cli.command("set-caption")
@click.argument("video_path", type=click.Path(exists=True))
@click.argument("caption_text")
@click.option("--hashtags", "-t", default="", help="Comma-separated hashtags")
@click.option("--platforms", "-p", default="", help="Comma-separated platforms")
@click.option("--schedule", "-s", default=None, help="Schedule time (YYYY-MM-DD HH:MM)")
def set_caption(video_path, caption_text, hashtags, platforms, schedule):
    """Save a caption to a video's YAML sidecar file."""
    video_path = Path(video_path)
    existing = load_metadata(video_path)
    meta = existing or VideoMetadata()

    meta.caption = caption_text
    if hashtags:
        meta.hashtags = [h.strip().lstrip("#") for h in hashtags.split(",")]
    if platforms:
        meta.platforms = [p.strip() for p in platforms.split(",")]
    if schedule:
        meta.schedule = schedule

    saved_path = save_metadata(video_path, meta)
    console.print(f"[green]Caption saved to:[/green] {saved_path}")


# ── vidpost swap-audio ────────────────────────────────────────

@cli.command("swap-audio")
@click.argument("video_path", type=click.Path(exists=True))
@click.argument("audio_path", type=click.Path(exists=True))
@click.option("--output", "-o", default=None, help="Output path (default: <video>_swapped.mp4 next to original)")
@click.option("--keep-original-audio", is_flag=True, help="Mix new audio with original at 10% instead of replacing")
@click.option("--volume", type=float, default=1.0, help="Volume of the new audio (0.0-2.0, default 1.0)")
@click.option("--overwrite", is_flag=True, help="Replace the original file (saves to video.mp4 directly)")
def swap_audio(video_path, audio_path, output, keep_original_audio, volume, overwrite):
    """Replace the audio track on a video with a royalty-free track.

    Trims or loops the audio to match the video duration.
    """
    import subprocess

    video_path = Path(video_path)
    audio_path = Path(audio_path)

    if overwrite:
        # Write to temp, then move into place
        output_path = video_path.with_suffix(".swapped.mp4")
    elif output:
        output_path = Path(output)
    else:
        output_path = video_path.with_stem(f"{video_path.stem}_swapped")

    # Get video duration to trim audio correctly
    from vidpost.captions.analyzer import get_video_duration
    duration = get_video_duration(video_path)

    console.print(f"Swapping audio on [bold]{video_path.name}[/bold] ({duration:.1f}s)...")

    if keep_original_audio:
        # Mix new audio at full volume with original at 10%
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1", "-i", str(audio_path),  # loop audio if shorter
            "-filter_complex",
            f"[1:a]volume={volume}[new];[0:a]volume=0.1[old];[new][old]amix=inputs=2:duration=first[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration),
            "-shortest",
            str(output_path),
        ]
    else:
        # Replace audio completely
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1", "-i", str(audio_path),
            "-filter_complex", f"[1:a]volume={volume}[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration),
            "-shortest",
            str(output_path),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]ffmpeg failed:[/red]\n{result.stderr[-500:]}")
        return

    if overwrite:
        output_path.replace(video_path)
        console.print(f"[green]Replaced audio in:[/green] {video_path}")
    else:
        console.print(f"[green]Saved to:[/green] {output_path}")


# ── vidpost post ──────────────────────────────────────────────

@cli.command()
@click.argument("video_path", type=click.Path(exists=True))
@click.option("--caption", "-c", default=None, help="Caption text")
@click.option("--hashtags", "-t", default=None, help="Comma-separated hashtags")
@click.option("--title", default=None, help="Title (YouTube only; ignored on other platforms)")
@click.option("--platforms", "-p", default=None, help="Comma-separated platforms (youtube,facebook,tiktok)")
@click.option("--schedule", "-s", default=None, help="Schedule time (YYYY-MM-DD HH:MM)")
@click.option("--dry-run", is_flag=True, help="Show what would happen without posting")
@click.option("--report-as-slot", default=None,
              help="Emit a dashboard status report under this slot name (run_kind=manual). "
                   "For unattended one-off runs (e.g. scheduled bat files).")
def post(video_path, caption, hashtags, title, platforms, schedule, dry_run, report_as_slot):
    """Post a video to one or more platforms."""
    video_path = Path(video_path)

    # Load metadata from sidecar if it exists
    meta = load_metadata(video_path)

    # CLI flags override sidecar values
    final_caption = caption or (meta.caption if meta else "")
    final_hashtags = (
        [h.strip().lstrip("#") for h in hashtags.split(",")]
        if hashtags else
        (meta.hashtags if meta else [])
    )

    config = load_config()
    if platforms:
        final_platforms = [p.strip() for p in platforms.split(",")]
    elif meta and meta.platforms:
        final_platforms = meta.platforms
    else:
        final_platforms = config["defaults"]["platforms"]

    schedule_time = schedule or (meta.schedule if meta else None)

    if not final_caption:
        console.print(
            f"[yellow]No caption found.[/yellow] Add an entry in [cyan]{video_path.parent / 'caption.txt'}[/cyan] "
            f"or pass --caption."
        )
        if not click.confirm("Post without a caption?"):
            return

    if dry_run:
        _print_dry_run(video_path, final_caption, final_hashtags, final_platforms, schedule_time)
        return

    started_at = datetime.now(_get_timezone())
    attempted = 0
    succeeded = 0
    errors: list[str] = []

    # Create post records and upload
    for platform_name in final_platforms:
        # Handle facebook:pagename syntax
        base_platform = platform_name.split(":")[0] if ":" in platform_name else platform_name
        try:
            platform_enum = Platform.from_str(base_platform)
        except ValueError:
            msg = f"{video_path.name} → {platform_name}: unknown platform"
            console.print(f"[red]Unknown platform: {platform_name}[/red]")
            errors.append(msg)
            continue

        scheduled_at = _parse_schedule(schedule_time) if schedule_time else None

        # Check for platform overrides
        platform_caption = final_caption
        platform_meta = {}
        if meta and meta.platform_overrides.get(platform_name):
            overrides = meta.platform_overrides[platform_name]
            platform_caption = overrides.get("caption", final_caption)
            platform_meta = dict(overrides)
        # Title precedence for YouTube: CLI --title > YAML override > captions.txt TITLE: line
        if platform_name == "youtube":
            if title:
                platform_meta["title"] = title
            elif "title" not in platform_meta and meta and meta.title:
                platform_meta["title"] = meta.title

        post_record = create_post(
            video_path=str(video_path),
            platform=platform_enum,
            caption=platform_caption,
            hashtags=final_hashtags,
            scheduled_at=scheduled_at,
            metadata_path=str(sidecar_path(video_path)) if meta else None,
            platform_target=platform_name,
        )

        if scheduled_at:
            console.print(
                f"[blue]Scheduled[/blue] {video_path.name} → {platform_name} "
                f"at {scheduled_at.strftime('%Y-%m-%d %H:%M %Z')} [dim](id: {post_record.id})[/dim]"
            )
        else:
            attempted += 1
            _upload_post(post_record, video_path, platform_caption, final_hashtags, platform_meta, platform_str=platform_name)
            updated = get_post(post_record.id)
            if updated and updated.status == PostStatus.POSTED:
                succeeded += 1
            else:
                err_msg = updated.error_message if updated else "unknown failure"
                errors.append(f"{video_path.name} → {platform_name}: {err_msg}")

    if report_as_slot and attempted > 0:
        overall = _classify_status(attempted, succeeded, errors)
        _emit_status_report(report_as_slot, started_at, overall, attempted, succeeded, errors,
                            details={"video": video_path.name, "targets": final_platforms},
                            run_kind="manual")


def _upload_post(post_record, video_path, caption, hashtags, extra_meta, platform_str=None):
    """Upload a single post to its platform."""
    from vidpost.platforms import get_platform

    # Use full platform string (e.g. "facebook:secondary") if provided
    platform_key = platform_str or post_record.platform.value
    platform = get_platform(platform_key)

    if not platform.is_authenticated():
        console.print(
            f"[red]Not authenticated with {post_record.platform.value}.[/red] "
            f"Run: [cyan]vidpost auth {post_record.platform.value}[/cyan]"
        )
        update_post_status(post_record.id, PostStatus.FAILED, error_message="Not authenticated")
        return

    update_post_status(post_record.id, PostStatus.UPLOADING)

    metadata = {"caption": caption, "hashtags": hashtags, **extra_meta}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Uploading to {post_record.platform.value}...", total=None)
        try:
            platform_post_id = asyncio.run(platform.upload_video(video_path, metadata))
            update_post_status(post_record.id, PostStatus.POSTED, platform_post_id=platform_post_id)
            progress.update(task, description="Done!")

            # Platform-specific success messages
            if post_record.platform == Platform.TIKTOK:
                console.print(
                    f"[green]Uploaded to TikTok inbox[/green] [dim](id: {post_record.id})[/dim]\n"
                    f"  [yellow]Open TikTok app to confirm and post.[/yellow]"
                )
            elif post_record.platform == Platform.YOUTUBE:
                url = f"https://youtube.com/watch?v={platform_post_id}" if platform_post_id else ""
                console.print(
                    f"[green]Posted to YouTube[/green] [dim](id: {post_record.id})[/dim]\n"
                    f"  {url}"
                )
            else:
                console.print(
                    f"[green]Posted to {post_record.platform.value}[/green] [dim](id: {post_record.id})[/dim]"
                )
        except Exception as e:
            update_post_status(post_record.id, PostStatus.FAILED, error_message=str(e))
            console.print(f"[red]Failed to upload to {post_record.platform.value}:[/red] {e}")


def _print_dry_run(video_path, caption, hashtags, platforms, schedule):
    """Print what would happen without actually posting."""
    console.print(Panel.fit(
        f"[bold]Video:[/bold] {video_path}\n"
        f"[bold]Caption:[/bold] {caption[:100]}{'...' if len(caption) > 100 else ''}\n"
        f"[bold]Hashtags:[/bold] {', '.join(f'#{h}' for h in hashtags)}\n"
        f"[bold]Platforms:[/bold] {', '.join(platforms)}\n"
        f"[bold]Schedule:[/bold] {schedule or 'Immediate'}",
        title="[yellow]DRY RUN[/yellow]",
    ))


# ── vidpost autopost ──────────────────────────────────────────

SLOT_TARGETS = {
    "morning": ["youtube", "facebook", "facebook:secondary"],
    "evening": ["facebook", "facebook:secondary"],
}


@cli.command()
@click.option("--slot", type=click.Choice(["morning", "evening"]), required=True,
              help="Which daily slot. morning=YT+both FB pages, evening=both FB pages.")
@click.option("--folder", default="~/videos/reels",
              help="Folder of source videos.")
@click.option("--count", default=6, type=int, help="Videos to post per run.")
@click.option("--targets", default=None,
              help="Comma-separated platform targets (overrides slot defaults).")
@click.option("--dry-run", is_flag=True, help="Show what would happen without posting.")
def autopost(slot, folder, count, targets, dry_run):
    """Pick N random un-posted videos and cross-post them to slot platforms.

    Skips videos already posted to every target. Once the pool is exhausted,
    falls back to least-recently-posted videos so repeats start over.
    """
    import random

    folder_path = Path(folder).expanduser()
    if not folder_path.is_dir():
        console.print(f"[red]Folder not found:[/red] {folder_path}")
        raise click.Abort()

    target_list = (
        [t.strip() for t in targets.split(",") if t.strip()]
        if targets else SLOT_TARGETS[slot]
    )
    target_set = set(target_list)

    videos = sorted(folder_path.glob("*.mp4"))
    if not videos:
        console.print(f"[red]No .mp4 files in {folder_path}[/red]")
        return

    # Tier 1: videos missing at least one target
    fresh = [v for v in videos if not target_set.issubset(get_posted_targets(str(v)))]
    if fresh:
        pool = fresh
        repeat_mode = False
    else:
        pool = videos  # full repeat once everything has hit every target
        repeat_mode = True

    picks = random.sample(pool, min(count, len(pool)))

    console.print(
        f"[bold]Slot:[/bold] {slot}  "
        f"[bold]Targets:[/bold] {', '.join(target_list)}  "
        f"[bold]Folder:[/bold] {folder_path}  "
        f"[bold]Pool:[/bold] {len(pool)} {'(repeat mode)' if repeat_mode else 'fresh'}"
    )

    plan = []
    for video in picks:
        meta = load_metadata(video)
        caption = meta.caption if meta else ""
        hashtags = meta.hashtags if meta else []
        already = get_posted_targets(str(video))
        targets_to_post = [t for t in target_list if t not in already] if not repeat_mode else target_list
        plan.append((video, meta, caption, hashtags, targets_to_post))

    table = Table(title=f"autopost — {slot} batch")
    table.add_column("#", style="dim")
    table.add_column("Video")
    table.add_column("Caption (first line)")
    table.add_column("Targets")
    for i, (video, meta, caption, hashtags, tgts) in enumerate(plan, 1):
        first_line = (caption.splitlines()[0] if caption else "[dim](no caption)[/dim]")[:60]
        table.add_row(str(i), video.name, first_line, ", ".join(tgts) or "[dim]all done[/dim]")
    console.print(table)

    if dry_run:
        console.print("[yellow]Dry run — nothing posted.[/yellow]")
        return

    started_at = datetime.now(_get_timezone())
    attempted = 0
    succeeded = 0
    errors: list[str] = []

    for video, meta, caption, hashtags, tgts in plan:
        if not tgts:
            continue
        if not caption:
            msg = f"{video.name}: no caption found, skipped"
            console.print(f"[yellow]Skipping {video.name} — no caption.[/yellow]")
            errors.append(msg)
            continue
        for target in tgts:
            base = target.split(":")[0]
            try:
                platform_enum = Platform.from_str(base)
            except ValueError:
                msg = f"{video.name} → {target}: unknown platform"
                console.print(f"[red]{msg}[/red]")
                errors.append(msg)
                continue

            platform_caption = caption
            platform_meta = {}
            if meta and meta.platform_overrides.get(target):
                overrides = meta.platform_overrides[target]
                platform_caption = overrides.get("caption", caption)
                platform_meta = dict(overrides)
            if base == "youtube":
                if "title" not in platform_meta and meta and meta.title:
                    platform_meta["title"] = meta.title

            post_record = create_post(
                video_path=str(video),
                platform=platform_enum,
                caption=platform_caption,
                hashtags=hashtags,
                metadata_path=str(sidecar_path(video)) if meta else None,
                platform_target=target,
            )
            attempted += 1
            _upload_post(post_record, video, platform_caption, hashtags, platform_meta, platform_str=target)
            updated = get_post(post_record.id)
            if updated and updated.status == PostStatus.POSTED:
                succeeded += 1
            else:
                err_msg = updated.error_message if updated else "unknown failure"
                errors.append(f"{video.name} → {target}: {err_msg}")

    console.print(f"\n[green]autopost done.[/green] {succeeded}/{attempted} uploads succeeded across {len(picks)} videos.")

    overall = _classify_status(attempted, succeeded, errors)
    _emit_status_report(slot, started_at, overall, attempted, succeeded, errors,
                        details={"picks": [v.name for v, *_ in plan], "targets": target_list})


def _classify_status(attempted: int, succeeded: int, errors: list[str]) -> str:
    if attempted == 0:
        return "failed"
    auth_failure = any("not authenticated" in e.lower() or "oauth" in e.lower() or "#200" in e
                       for e in errors)
    if auth_failure and succeeded == 0:
        return "auth_failed"
    if succeeded == attempted:
        return "ok"
    if succeeded == 0:
        return "failed"
    return "partial"


def _emit_status_report(
    slot: str,
    ran_at: datetime,
    status: str,
    attempted: int,
    succeeded: int,
    errors: list[str],
    details: dict,
    run_kind: str = "automated",
) -> None:
    """POST a status record to VIDPOST_STATUS_URL if configured. Best-effort."""
    import os
    import httpx

    url = os.environ.get("VIDPOST_STATUS_URL")
    token = os.environ.get("VIDPOST_STATUS_TOKEN")
    if not url or not token:
        return

    payload = {
        "slot": slot,
        "run_kind": run_kind,
        "status": status,
        "ran_at": ran_at.isoformat(),
        "posts_attempted": attempted,
        "posts_succeeded": succeeded,
        "errors": errors,
        "details": details,
    }
    try:
        r = httpx.post(url, json=payload, timeout=15,
                       headers={"Authorization": f"Bearer {token}"})
        if r.status_code >= 300:
            console.print(f"[yellow]Status report HTTP {r.status_code}: {r.text[:200]}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Could not POST status report: {e}[/yellow]")


# ── vidpost batch ─────────────────────────────────────────────

@cli.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False))
@click.option("--platforms", "-p", default=None, help="Override platforms for all videos")
@click.option("--schedule-start", default=None, help="Start time for scheduling (YYYY-MM-DD HH:MM)")
@click.option("--schedule-interval", default=None, help="Interval between posts (e.g., 2d, 12h, 1d)")
@click.option("--generate-metadata", is_flag=True, help="Generate YAML sidecars for videos without them")
@click.option("--dry-run", is_flag=True, help="Show what would happen without posting")
@click.option("--repost", is_flag=True, help="Ignore 'already posted' status, create new posts")
def batch(folder, platforms, schedule_start, schedule_interval, generate_metadata, dry_run, repost):
    """Process an entire folder of videos."""
    folder = Path(folder)
    videos = find_videos(folder)

    if not videos:
        console.print(f"[red]No video files found in {folder}[/red]")
        return

    console.print(f"Found [bold]{len(videos)}[/bold] videos in {folder}\n")

    # Generate metadata sidecars if requested
    if generate_metadata:
        for video in videos:
            if not sidecar_path(video).exists():
                meta = VideoMetadata()
                save_metadata(video, meta)
                console.print(f"  [green]Created:[/green] {sidecar_path(video)}")
            else:
                console.print(f"  [dim]Exists:[/dim] {sidecar_path(video)}")
        console.print(f"\n[green]Sidecars ready.[/green] Edit them with captions, then run batch again.")
        return

    # Parse scheduling
    config = load_config()
    final_platforms = platforms.split(",") if platforms else config["defaults"]["platforms"]
    schedule_dt = _parse_schedule(schedule_start) if schedule_start else None
    interval = _parse_interval(schedule_interval) if schedule_interval else None

    # Process each video
    table = Table(title="Batch Processing Plan")
    table.add_column("Video", style="cyan")
    table.add_column("Caption", max_width=40)
    table.add_column("Platforms")
    table.add_column("Schedule")
    table.add_column("Status")

    for i, video in enumerate(videos):
        meta = load_metadata(video)
        video_caption = meta.caption if meta else ""
        video_platforms = meta.platforms if meta and meta.platforms else final_platforms
        video_schedule = None

        if schedule_dt and interval:
            video_schedule = schedule_dt + (interval * i)
        elif meta and meta.schedule:
            video_schedule = _parse_schedule(meta.schedule)

        status = "ready" if video_caption else "[yellow]no caption[/yellow]"

        table.add_row(
            video.name,
            (video_caption[:37] + "...") if len(video_caption) > 40 else video_caption,
            ", ".join(video_platforms),
            video_schedule.strftime("%Y-%m-%d %H:%M") if video_schedule else "immediate",
            status,
        )

        if not dry_run and video_caption:
            for platform_name in video_platforms:
                base_platform = platform_name.split(":")[0] if ":" in platform_name else platform_name
                try:
                    platform_enum = Platform.from_str(base_platform)
                except ValueError:
                    continue

                # Check for platform overrides
                platform_caption = video_caption
                if meta and meta.platform_overrides.get(platform_name):
                    platform_caption = meta.platform_overrides[platform_name].get("caption", video_caption)

                post_record = create_post(
                    video_path=str(video),
                    platform=platform_enum,
                    caption=platform_caption,
                    hashtags=meta.hashtags if meta else [],
                    scheduled_at=video_schedule,
                    metadata_path=str(sidecar_path(video)),
                )

                if not video_schedule:
                    _upload_post(post_record, video, platform_caption, meta.hashtags if meta else [], {}, platform_str=platform_name)

    console.print(table)

    if dry_run:
        console.print("\n[yellow]DRY RUN — no posts created.[/yellow]")


def _parse_interval(interval_str: str) -> timedelta:
    """Parse interval string like '2d', '12h', '1d12h' into timedelta."""
    total = timedelta()
    current_num = ""
    for char in interval_str:
        if char.isdigit():
            current_num += char
        elif char == "d":
            total += timedelta(days=int(current_num or "0"))
            current_num = ""
        elif char == "h":
            total += timedelta(hours=int(current_num or "0"))
            current_num = ""
        elif char == "m":
            total += timedelta(minutes=int(current_num or "0"))
            current_num = ""
    return total


# ── vidpost schedule ──────────────────────────────────────────

@cli.group("schedule")
def schedule_group():
    """Manage scheduled posts."""
    pass


@schedule_group.command("list")
def schedule_list():
    """Show all scheduled posts."""
    posts = get_scheduled_posts()
    if not posts:
        console.print("[dim]No scheduled posts.[/dim]")
        return

    table = Table(title="Scheduled Posts")
    table.add_column("ID", style="dim")
    table.add_column("Video")
    table.add_column("Platform")
    table.add_column("Scheduled For")
    table.add_column("Caption", max_width=30)

    for post in posts:
        table.add_row(
            post.id,
            Path(post.video_path).name,
            post.platform.value,
            post.scheduled_at.strftime("%Y-%m-%d %H:%M") if post.scheduled_at else "—",
            (post.caption[:27] + "...") if len(post.caption) > 30 else post.caption,
        )

    console.print(table)


@schedule_group.command("cancel")
@click.argument("post_id")
def schedule_cancel(post_id):
    """Cancel a scheduled post."""
    post = get_post(post_id)
    if not post:
        console.print(f"[red]Post {post_id} not found.[/red]")
        return
    if post.status != PostStatus.SCHEDULED:
        console.print(f"[yellow]Post {post_id} is {post.status.value}, not scheduled.[/yellow]")
        return
    delete_post(post_id)
    console.print(f"[green]Cancelled scheduled post {post_id}[/green]")


@schedule_group.command("retry")
@click.argument("post_id")
def schedule_retry(post_id):
    """Retry a failed post."""
    post = get_post(post_id)
    if not post:
        console.print(f"[red]Post {post_id} not found.[/red]")
        return
    if post.status != PostStatus.FAILED:
        console.print(f"[yellow]Post {post_id} is {post.status.value}, not failed.[/yellow]")
        return

    update_post_status(post_id, PostStatus.PENDING)
    video_path = Path(post.video_path)
    _upload_post(post, video_path, post.caption, post.hashtags, {})


# ── vidpost retry-failed ──────────────────────────────────────

@cli.command("retry-failed")
@click.option("--platform", "-p", default=None, help="Only retry posts on a specific platform (e.g. youtube, facebook)")
@click.option("--limit", "-n", type=int, default=50, help="Max number of posts to retry")
@click.option("--dry-run", is_flag=True, help="Show what would be retried without doing it")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt (for unattended runs)")
@click.option("--report-as-slot", default=None,
              help="Emit a dashboard status report under this slot name (run_kind=manual). "
                   "For unattended one-off runs (e.g. scheduled bat files).")
def retry_failed(platform, limit, dry_run, yes, report_as_slot):
    """Retry all failed posts at once."""
    failed = get_posts(status=PostStatus.FAILED, limit=limit)

    # Filter by platform if requested
    if platform:
        base = platform.split(":")[0].lower()
        failed = [p for p in failed if p.platform.value == base]

    if not failed:
        console.print("[green]No failed posts to retry.[/green]")
        return

    # Display what we're about to retry
    table = Table(title=f"Failed Posts ({len(failed)})")
    table.add_column("ID", style="dim")
    table.add_column("Video")
    table.add_column("Platform")
    table.add_column("Error", max_width=40)
    for post in failed:
        table.add_row(
            post.id,
            Path(post.video_path).name,
            post.platform.value,
            (post.error_message or "")[:40],
        )
    console.print(table)

    if dry_run:
        console.print("\n[yellow]DRY RUN — nothing retried.[/yellow]")
        return

    if not yes and not click.confirm(f"\nRetry {len(failed)} failed posts?", default=True):
        return

    started_at = datetime.now(_get_timezone())
    success_count = 0
    fail_count = 0
    errors: list[str] = []
    for post in failed:
        video_path = Path(post.video_path)
        if not video_path.exists():
            msg = f"{Path(post.video_path).name} → {post.platform.value}: video file missing"
            console.print(f"[red]Skipping {post.id}: video file missing ({video_path})[/red]")
            fail_count += 1
            errors.append(msg)
            continue
        console.print(f"\nRetrying [cyan]{post.id}[/cyan] → {post.platform.value}...")
        update_post_status(post.id, PostStatus.PENDING, error_message=None)
        try:
            _upload_post(post, video_path, post.caption, post.hashtags, {})
            refreshed = get_post(post.id)
            if refreshed and refreshed.status == PostStatus.POSTED:
                success_count += 1
            else:
                fail_count += 1
                err = (refreshed.error_message if refreshed else "") or "unknown failure"
                errors.append(f"{video_path.name} → {post.platform.value}: {err}")
        except Exception as e:
            fail_count += 1
            errors.append(f"{video_path.name} → {post.platform.value}: {e}")
            console.print(f"[red]Retry failed: {e}[/red]")

    console.print(
        f"\n[green]Succeeded:[/green] {success_count}   "
        f"[red]Still failing:[/red] {fail_count}"
    )

    attempted = success_count + fail_count
    if report_as_slot and attempted > 0:
        overall = _classify_status(attempted, success_count, errors)
        _emit_status_report(report_as_slot, started_at, overall, attempted, success_count, errors,
                            details={"platform_filter": platform, "post_ids": [p.id for p in failed]},
                            run_kind="manual")


# ── vidpost status ────────────────────────────────────────────

@cli.command()
@click.argument("post_id", required=False)
def status(post_id):
    """Check post status. Shows recent history or details for a specific post."""
    if post_id:
        post = get_post(post_id)
        if not post:
            console.print(f"[red]Post {post_id} not found.[/red]")
            return
        _print_post_detail(post)
    else:
        posts = get_posts(limit=20)
        if not posts:
            console.print("[dim]No posts yet.[/dim]")
            return
        _print_posts_table(posts)


def _print_post_detail(post):
    """Print detailed info for a single post."""
    status_colors = {
        PostStatus.PENDING: "yellow",
        PostStatus.SCHEDULED: "blue",
        PostStatus.UPLOADING: "cyan",
        PostStatus.POSTED: "green",
        PostStatus.FAILED: "red",
    }
    color = status_colors.get(post.status, "white")

    lines = [
        f"[bold]ID:[/bold] {post.id}",
        f"[bold]Video:[/bold] {post.video_path}",
        f"[bold]Platform:[/bold] {post.platform.value}",
        f"[bold]Status:[/bold] [{color}]{post.status.value}[/{color}]",
        f"[bold]Caption:[/bold] {post.caption[:100]}",
    ]
    if post.hashtags:
        lines.append(f"[bold]Hashtags:[/bold] {', '.join(f'#{h}' for h in post.hashtags)}")
    if post.scheduled_at:
        lines.append(f"[bold]Scheduled:[/bold] {post.scheduled_at}")
    if post.posted_at:
        lines.append(f"[bold]Posted:[/bold] {post.posted_at}")
    if post.platform_post_id:
        lines.append(f"[bold]Platform ID:[/bold] {post.platform_post_id}")
    if post.error_message:
        lines.append(f"[bold red]Error:[/bold red] {post.error_message}")

    console.print(Panel.fit("\n".join(lines), title=f"Post {post.id}"))


def _print_posts_table(posts):
    """Print a table of posts."""
    table = Table(title="Recent Posts")
    table.add_column("ID", style="dim")
    table.add_column("Video")
    table.add_column("Platform")
    table.add_column("Status")
    table.add_column("Time")

    status_colors = {
        "pending": "yellow", "scheduled": "blue", "uploading": "cyan",
        "posted": "green", "failed": "red",
    }

    for post in posts:
        color = status_colors.get(post.status.value, "white")
        time_str = ""
        if post.posted_at:
            time_str = post.posted_at.strftime("%m/%d %H:%M")
        elif post.scheduled_at:
            time_str = f"→ {post.scheduled_at.strftime('%m/%d %H:%M')}"
        elif post.created_at:
            time_str = post.created_at.strftime("%m/%d %H:%M")

        table.add_row(
            post.id,
            Path(post.video_path).name,
            post.platform.value,
            f"[{color}]{post.status.value}[/{color}]",
            time_str,
        )

    console.print(table)


# ── vidpost daemon ────────────────────────────────────────────

@cli.group()
def daemon():
    """Manage the background scheduler daemon."""
    pass


@daemon.command("start")
def daemon_start():
    """Start the scheduler daemon."""
    from vidpost.scheduler import start_daemon, daemon_status

    status = daemon_status()
    if status["running"]:
        console.print(f"[yellow]Daemon already running (PID: {status['pid']})[/yellow]")
        return

    console.print("[green]Starting scheduler daemon...[/green]")
    start_daemon()


@daemon.command("stop")
def daemon_stop():
    """Stop the scheduler daemon."""
    from vidpost.scheduler import stop_daemon

    if stop_daemon():
        console.print("[green]Daemon stopped.[/green]")
    else:
        console.print("[yellow]No daemon running.[/yellow]")


@daemon.command("status")
def daemon_check():
    """Check if the scheduler daemon is running."""
    from vidpost.scheduler import daemon_status

    status = daemon_status()
    if status["running"]:
        console.print(f"[green]Daemon running[/green] (PID: {status['pid']})")

        # Show scheduled jobs count
        scheduled = get_scheduled_posts()
        console.print(f"  Scheduled posts: {len(scheduled)}")
    else:
        console.print("[dim]Daemon not running.[/dim]")


@cli.command("analyze-timing")
@click.option("--page", default=None, help="Facebook page name (e.g. secondary). Defaults to primary page.")
@click.option("--days", "-d", type=int, default=30, help="How many days of history to analyze (default 30).")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON instead of tables.")
def analyze_timing(page, days, as_json):
    """Analyze best Facebook posting times from Page Insights + post engagement."""
    import json as _json
    from vidpost.platforms.facebook import FacebookPlatform

    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    fb = FacebookPlatform(page=page)

    with console.status(f"[cyan]Fetching {days} days of Facebook insights..."):
        result = asyncio.run(fb.analyze_timing(days=days))

    if as_json:
        click.echo(_json.dumps(result, indent=2, default=str))
        return

    console.print(Panel.fit(
        f"Analyzed [bold]{result['sample_size']}[/bold] posts over [bold]{result['days_analyzed']}[/bold] days",
        title="Facebook Timing Analysis",
    ))

    rec = result["recommendations"]
    if rec["best_hours"] or rec["best_days"]:
        hours = ", ".join(f"{h:02d}:00" for h in rec["best_hours"]) or "—"
        daynames = ", ".join(DAYS[d] for d in rec["best_days"]) or "—"
        console.print(f"[green]Best hours:[/green] {hours}")
        console.print(f"[green]Best days:[/green]  {daynames}\n")

    hour_stats = result["post_engagement_by_hour"]
    fans = result["fans_online_by_hour"]
    if hour_stats or fans:
        t = Table(title="Engagement by Hour (local time)")
        t.add_column("Hour", justify="right")
        t.add_column("Posts", justify="right")
        t.add_column("Avg engagement", justify="right")
        t.add_column("Fans online (avg)", justify="right")
        for h in range(24):
            row = hour_stats.get(h, {})
            fan = fans.get(h) if isinstance(fans, dict) else None
            if fan is None and isinstance(fans, dict):
                fan = fans.get(str(h))
            if not row and fan is None:
                continue
            t.add_row(
                f"{h:02d}",
                str(row.get("count", "")),
                str(row.get("avg_engagement", "")),
                f"{fan:.0f}" if fan is not None else "",
            )
        console.print(t)

    dow_stats = result["post_engagement_by_dow"]
    if dow_stats:
        t = Table(title="Engagement by Day of Week")
        t.add_column("Day")
        t.add_column("Posts", justify="right")
        t.add_column("Avg engagement", justify="right")
        for d in range(7):
            row = dow_stats.get(d)
            if not row:
                continue
            t.add_row(DAYS[d], str(row["count"]), str(row["avg_engagement"]))
        console.print(t)

    if result["top_posts"]:
        t = Table(title="Top Posts")
        t.add_column("Time")
        t.add_column("Engagement", justify="right")
        t.add_column("Message")
        for p in result["top_posts"]:
            t.add_row(p["time"], str(p["engagement"]), p["message"] or "[dim]—[/dim]")
        console.print(t)


def _get_comment_platform(platform: str, page: str | None):
    """Route to the right platform class for comment operations."""
    if platform == "instagram":
        from vidpost.platforms.instagram import InstagramPlatform
        return InstagramPlatform(account=page)
    if platform == "youtube":
        from vidpost.platforms.youtube import YouTubePlatform
        return YouTubePlatform()
    from vidpost.platforms.facebook import FacebookPlatform
    return FacebookPlatform(page=page)


@cli.group()
def comments():
    """Manage Facebook, Instagram, and YouTube comment replies."""
    pass


@comments.command("list")
@click.option("--platform", type=click.Choice(["facebook", "instagram", "youtube"]), default="facebook", help="Which platform.")
@click.option("--page", default=None, help="Page/account name (e.g. secondary). Default: primary.")
@click.option("--days", "-d", type=int, default=14, help="Days of history the API checks (default 14).")
@click.option("--all", "show_all", is_flag=True, help="Show all comments regardless of cursor (does not advance cursor).")
@click.option("--since", default=None, help="Only show comments newer than this (ISO timestamp or e.g. '2d', '6h'). Overrides cursor.")
@click.option("--no-mark", is_flag=True, help="Don't update the cursor after this pull (peek only).")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (pipe to reply-batch).")
@click.option("--save", type=click.Path(), default=None, help="Save JSON output to a file.")
def comments_list(platform, page, days, show_all, since, no_mark, as_json, save):
    """List comments since the last pull (incremental). Use --all for everything."""
    import json as _json
    import re
    from datetime import datetime, timedelta, timezone
    from vidpost.db import get_comments_cursor, set_comments_cursor

    plat = _get_comment_platform(platform, page)
    with console.status(f"[cyan]Fetching {platform} pending comments ({days}d)..."):
        all_pending = asyncio.run(plat.get_pending_comments(days=days))

    # Resolve filter cutoff
    cursor_ts = get_comments_cursor(platform, page)
    cutoff: datetime | None = None
    cutoff_source = ""
    if show_all:
        cutoff = None
        cutoff_source = "all"
    elif since:
        # Accept ISO format or relative like '2d', '6h', '30m'
        m = re.fullmatch(r"(\d+)\s*([dhm])", since.strip().lower())
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
            cutoff = datetime.now(timezone.utc) - delta
            cutoff_source = f"--since {since}"
        else:
            try:
                cutoff = datetime.fromisoformat(since)
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=timezone.utc)
                cutoff_source = f"--since {since}"
            except ValueError:
                raise click.BadParameter(f"--since must be ISO timestamp or like '2d', '6h', '30m'; got {since!r}")
    elif cursor_ts is not None:
        cutoff = cursor_ts if cursor_ts.tzinfo else cursor_ts.replace(tzinfo=timezone.utc)
        cutoff_source = f"since last pull ({cutoff.isoformat(timespec='minutes')})"
    else:
        cutoff = None
        cutoff_source = "first pull (showing all)"

    def _parse_time(s: str) -> datetime | None:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    if cutoff is not None:
        filtered = [c for c in all_pending if (t := _parse_time(c.get("time", ""))) and t > cutoff]
    else:
        filtered = list(all_pending)

    pending = filtered

    if save:
        Path(save).write_text(_json.dumps(pending, indent=2))
        console.print(f"[green]Saved {len(pending)} comments to {save}[/green]")
    if as_json:
        click.echo(_json.dumps(pending, indent=2))
    elif not pending:
        console.print(f"[green]No new comments {cutoff_source}.[/green]")
        if all_pending and cutoff is not None:
            console.print(f"[dim]({len(all_pending)} older comments hidden — use --all to see everything)[/dim]")
    else:
        t = Table(title=f"Comments {cutoff_source} ({len(pending)})", show_lines=True)
        t.add_column("#", justify="right", style="dim")
        t.add_column("Post")
        t.add_column("Author")
        t.add_column("Comment")
        t.add_column("Comment ID", style="dim")
        for i, c in enumerate(pending, 1):
            post_label = c["post_title"] or c["post_caption"] or "(no title)"
            t.add_row(
                str(i),
                post_label[:40],
                c["author"][:20],
                c["message"][:80] + ("…" if len(c["message"]) > 80 else ""),
                c["comment_id"],
            )
        console.print(t)
        if cutoff is not None and len(all_pending) > len(pending):
            console.print(f"[dim]({len(all_pending) - len(pending)} older comments hidden — use --all to see everything)[/dim]")

    # Advance cursor unless --all or --no-mark, using max comment time we just saw
    if not show_all and not no_mark and pending:
        max_t = max((t for c in pending if (t := _parse_time(c.get("time", "")))), default=None)
        if max_t is not None:
            set_comments_cursor(platform, page, max_t)


@comments.command("reply")
@click.argument("comment_id")
@click.argument("message")
@click.option("--platform", type=click.Choice(["facebook", "instagram", "youtube"]), default="facebook")
@click.option("--page", default=None, help="Page/account name.")
def comments_reply(comment_id, message, platform, page):
    """Reply to a specific comment by comment_id."""
    plat = _get_comment_platform(platform, page)
    res = asyncio.run(plat.reply_to_comment(comment_id, message))
    if "error" in res:
        console.print(f"[red]Error:[/red] {res['error'].get('message', res['error'])}")
        raise click.Abort()
    console.print(f"[green]✅ Replied[/green] reply_id={res.get('id','')}")


@comments.command("delete")
@click.argument("comment_id")
@click.option("--platform", type=click.Choice(["facebook", "instagram", "youtube"]), default="facebook")
@click.option("--page", default=None, help="Page/account name.")
@click.confirmation_option(prompt="Delete this comment?")
def comments_delete(comment_id, platform, page):
    """Delete a comment (typically one of your own replies)."""
    plat = _get_comment_platform(platform, page)
    if asyncio.run(plat.delete_comment(comment_id)):
        console.print("[green]✅ Deleted[/green]")
    else:
        console.print("[red]❌ Delete failed[/red]")
        raise click.Abort()


@comments.command("reply-batch")
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("--platform", type=click.Choice(["facebook", "instagram", "youtube"]), default="facebook")
@click.option("--page", default=None, help="Page/account name.")
@click.option("--dry-run", is_flag=True, help="Print what would be sent without sending.")
@click.option("--min-delay", type=float, default=2.0, help="Min seconds between sends (default 2).")
@click.option("--max-delay", type=float, default=4.0, help="Max seconds between sends (default 4).")
@click.option("--no-delay", is_flag=True, help="Disable the randomized delay between sends.")
def comments_reply_batch(plan_file, platform, page, dry_run, min_delay, max_delay, no_delay):
    """Batch-reply from a JSON file. Format: [{"comment_id": "...", "message": "..."}]"""
    import json as _json
    import random
    import time

    plan = _json.loads(Path(plan_file).read_text())
    if not isinstance(plan, list):
        raise click.BadParameter("Plan file must be a JSON list")

    plat = _get_comment_platform(platform, page)
    sent, failed = 0, 0
    total = len(plan)
    for i, item in enumerate(plan, 1):
        cid = item.get("comment_id")
        msg = item.get("message")
        if not cid or not msg:
            console.print(f"[yellow][{i}] skipped — missing comment_id or message[/yellow]")
            continue
        if dry_run:
            console.print(f"[dim][{i}] would reply to {cid}:[/dim] {msg[:80]}")
            continue
        res = asyncio.run(plat.reply_to_comment(cid, msg))
        if "error" in res:
            console.print(f"[red][{i}] ❌ {cid}:[/red] {res['error'].get('message','')}")
            failed += 1
        else:
            console.print(f"[green][{i}] ✅ {cid}[/green] → {msg[:60]}")
            sent += 1
        # Human-like jitter between sends (skip after last)
        if not no_delay and i < total:
            delay = random.uniform(min_delay, max_delay)
            console.print(f"[dim]   ⏳ waiting {delay:.1f}s...[/dim]")
            time.sleep(delay)
    console.print(f"\n[bold]Sent: {sent}  Failed: {failed}  Total: {total}[/bold]")


if __name__ == "__main__":
    cli()
