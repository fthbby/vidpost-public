"""APScheduler-based job scheduling for timed video posts."""

import asyncio
import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from vidpost.config import CONFIG_DIR, load_config
from vidpost.db import get_post, get_scheduled_posts, update_post_status
from vidpost.models import Platform, PostStatus

PID_FILE = CONFIG_DIR / "daemon.pid"


def _get_timezone() -> ZoneInfo:
    config = load_config()
    return ZoneInfo(config["defaults"]["timezone"])


def _execute_post(post_id: str) -> None:
    """Execute a scheduled post — called by APScheduler."""
    post = get_post(post_id)
    if not post:
        return
    if post.status != PostStatus.SCHEDULED:
        return

    update_post_status(post_id, PostStatus.UPLOADING)

    try:
        from vidpost.platforms import get_platform

        platform = get_platform(post.platform.value)
        video_path = Path(post.video_path)

        if not video_path.exists():
            update_post_status(post_id, PostStatus.FAILED, error_message=f"Video file not found: {video_path}")
            return

        metadata = {
            "caption": post.caption,
            "hashtags": post.hashtags,
        }

        # Run async upload in sync context
        loop = asyncio.new_event_loop()
        try:
            platform_post_id = loop.run_until_complete(
                platform.upload_video(video_path, metadata)
            )
            update_post_status(post_id, PostStatus.POSTED, platform_post_id=platform_post_id)
        except Exception as e:
            update_post_status(post_id, PostStatus.FAILED, error_message=str(e))
        finally:
            loop.close()

    except Exception as e:
        update_post_status(post_id, PostStatus.FAILED, error_message=str(e))


def create_scheduler() -> BackgroundScheduler:
    """Create and configure the APScheduler instance."""
    scheduler = BackgroundScheduler(timezone=str(_get_timezone()))
    return scheduler


def load_scheduled_jobs(scheduler: BackgroundScheduler) -> int:
    """Load all scheduled posts from DB into the scheduler. Returns count."""
    posts = get_scheduled_posts()
    count = 0
    tz = _get_timezone()

    for post in posts:
        if not post.scheduled_at:
            continue
        # If the scheduled time is in the past, execute immediately
        now = datetime.now(tz)
        scheduled = post.scheduled_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=tz)

        if scheduled <= now:
            _execute_post(post.id)
        else:
            trigger = DateTrigger(run_date=scheduled)
            scheduler.add_job(
                _execute_post,
                trigger=trigger,
                args=[post.id],
                id=f"post_{post.id}",
                replace_existing=True,
            )
            count += 1

    return count


def start_daemon() -> None:
    """Start the scheduler daemon in the foreground."""
    from vidpost.db import init_db
    init_db()

    scheduler = create_scheduler()
    count = load_scheduled_jobs(scheduler)
    scheduler.start()

    print(f"Scheduler started with {count} pending jobs.")
    print("Press Ctrl+C to stop.")

    # Write PID file
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    def shutdown(signum, frame):
        print("\nShutting down scheduler...")
        scheduler.shutdown()
        PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep running
    try:
        while True:
            import time
            time.sleep(60)
            # Reload any new scheduled posts
            load_scheduled_jobs(scheduler)
    except (KeyboardInterrupt, SystemExit):
        shutdown(None, None)


def stop_daemon() -> bool:
    """Stop the running scheduler daemon. Returns True if stopped."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        return True
    except (ProcessLookupError, ValueError):
        PID_FILE.unlink(missing_ok=True)
        return False


def daemon_status() -> dict:
    """Check if the daemon is running."""
    if not PID_FILE.exists():
        return {"running": False}
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        return {"running": True, "pid": pid}
    except (ProcessLookupError, ValueError):
        PID_FILE.unlink(missing_ok=True)
        return {"running": False}
