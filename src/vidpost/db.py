"""SQLite database for post queue, history, and auth tokens."""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from vidpost.config import CONFIG_DIR
from vidpost.models import Platform, PostRecord, PostStatus

DB_PATH = CONFIG_DIR / "vidpost.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            video_path TEXT NOT NULL,
            caption TEXT,
            hashtags TEXT,
            platform TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            scheduled_at DATETIME,
            posted_at DATETIME,
            platform_post_id TEXT,
            error_message TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            metadata_path TEXT
        );

        CREATE TABLE IF NOT EXISTS auth_tokens (
            platform TEXT PRIMARY KEY,
            access_token TEXT,
            refresh_token TEXT,
            expires_at DATETIME,
            extra_data TEXT
        );

        CREATE TABLE IF NOT EXISTS comment_cursors (
            platform TEXT NOT NULL,
            page TEXT NOT NULL DEFAULT '',
            last_pulled_at TEXT NOT NULL,
            PRIMARY KEY (platform, page)
        );

        CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
        CREATE INDEX IF NOT EXISTS idx_posts_scheduled ON posts(scheduled_at);
        CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(posts)").fetchall()}
    if "platform_target" not in cols:
        conn.execute("ALTER TABLE posts ADD COLUMN platform_target TEXT")
    conn.commit()
    conn.close()


def _row_to_post(row: sqlite3.Row) -> PostRecord:
    return PostRecord(
        id=row["id"],
        video_path=row["video_path"],
        caption=row["caption"] or "",
        hashtags=json.loads(row["hashtags"]) if row["hashtags"] else [],
        platform=Platform(row["platform"]),
        status=PostStatus(row["status"]),
        scheduled_at=datetime.fromisoformat(row["scheduled_at"]) if row["scheduled_at"] else None,
        posted_at=datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
        platform_post_id=row["platform_post_id"],
        error_message=row["error_message"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        metadata_path=row["metadata_path"],
    )


def create_post(
    video_path: str,
    platform: Platform,
    caption: str = "",
    hashtags: list[str] | None = None,
    scheduled_at: datetime | None = None,
    metadata_path: str | None = None,
    platform_target: str | None = None,
) -> PostRecord:
    post_id = str(uuid.uuid4())[:8]
    conn = get_connection()
    status = PostStatus.SCHEDULED if scheduled_at else PostStatus.PENDING
    conn.execute(
        """INSERT INTO posts (id, video_path, caption, hashtags, platform, status, scheduled_at, metadata_path, platform_target)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            post_id,
            str(video_path),
            caption,
            json.dumps(hashtags or []),
            platform.value,
            status.value,
            scheduled_at.isoformat() if scheduled_at else None,
            metadata_path,
            platform_target or platform.value,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    conn.close()
    return _row_to_post(row)


def get_posted_targets(video_path: str) -> set[str]:
    """Return the set of platform_target strings (e.g. 'facebook', 'facebook:secondary',
    'youtube') that this video has already been successfully posted to."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT DISTINCT COALESCE(platform_target, platform) AS target
           FROM posts WHERE video_path = ? AND status = 'posted'""",
        (str(video_path),),
    ).fetchall()
    conn.close()
    return {r["target"] for r in rows}


def update_post_status(
    post_id: str,
    status: PostStatus,
    platform_post_id: str | None = None,
    error_message: str | None = None,
) -> None:
    conn = get_connection()
    updates = {"status": status.value}
    if status == PostStatus.POSTED:
        updates["posted_at"] = datetime.now().isoformat()
    if platform_post_id:
        updates["platform_post_id"] = platform_post_id
    if error_message:
        updates["error_message"] = error_message
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE posts SET {set_clause} WHERE id = ?",
        (*updates.values(), post_id),
    )
    conn.commit()
    conn.close()


def get_post(post_id: str) -> PostRecord | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    conn.close()
    return _row_to_post(row) if row else None


def get_posts(
    status: PostStatus | None = None,
    platform: Platform | None = None,
    limit: int = 50,
) -> list[PostRecord]:
    conn = get_connection()
    query = "SELECT * FROM posts WHERE 1=1"
    params: list = []
    if status:
        query += " AND status = ?"
        params.append(status.value)
    if platform:
        query += " AND platform = ?"
        params.append(platform.value)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_post(row) for row in rows]


def get_scheduled_posts() -> list[PostRecord]:
    return get_posts(status=PostStatus.SCHEDULED)


def get_pending_posts() -> list[PostRecord]:
    return get_posts(status=PostStatus.PENDING)


def delete_post(post_id: str) -> bool:
    conn = get_connection()
    cursor = conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


# Auth token management

def save_auth_token(
    platform: str,
    access_token: str,
    refresh_token: str | None = None,
    expires_at: datetime | None = None,
    extra_data: dict | None = None,
) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO auth_tokens (platform, access_token, refresh_token, expires_at, extra_data)
           VALUES (?, ?, ?, ?, ?)""",
        (
            platform,
            access_token,
            refresh_token,
            expires_at.isoformat() if expires_at else None,
            json.dumps(extra_data) if extra_data else None,
        ),
    )
    conn.commit()
    conn.close()


def get_auth_token(platform: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM auth_tokens WHERE platform = ?", (platform,)).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "platform": row["platform"],
        "access_token": row["access_token"],
        "refresh_token": row["refresh_token"],
        "expires_at": datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        "extra_data": json.loads(row["extra_data"]) if row["extra_data"] else {},
    }


def delete_auth_token(platform: str) -> bool:
    conn = get_connection()
    cursor = conn.execute("DELETE FROM auth_tokens WHERE platform = ?", (platform,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_comments_cursor(platform: str, page: str | None = None) -> datetime | None:
    """Return the last_pulled_at timestamp for a (platform, page) cursor, or None if unset.

    `page` is the page/account name (e.g. 'secondary' for an additional Facebook page,
    or None/empty for the default page).
    """
    page_key = (page or "").lower()
    conn = get_connection()
    row = conn.execute(
        "SELECT last_pulled_at FROM comment_cursors WHERE platform = ? AND page = ?",
        (platform, page_key),
    ).fetchone()
    conn.close()
    if not row:
        return None
    raw = row["last_pulled_at"]
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def set_comments_cursor(platform: str, page: str | None, last_pulled_at: datetime) -> None:
    """Upsert the last_pulled_at timestamp for a (platform, page) cursor."""
    page_key = (page or "").lower()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO comment_cursors (platform, page, last_pulled_at)
        VALUES (?, ?, ?)
        ON CONFLICT(platform, page) DO UPDATE SET last_pulled_at = excluded.last_pulled_at
        """,
        (platform, page_key, last_pulled_at.isoformat()),
    )
    conn.commit()
    conn.close()
