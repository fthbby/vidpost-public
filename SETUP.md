# Setup

vidpost runs the same on Mac and Windows. The Python source uses `pathlib` and `Path.home()` throughout, so no path edits are needed in the code.

## Prerequisites

| Tool | Mac | Windows |
|------|-----|---------|
| Python 3.11+ | `brew install python@3.11` | [python.org installer](https://www.python.org/downloads/) or `pyenv-win` |
| `uv` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| ffmpeg | `brew install ffmpeg` | `winget install ffmpeg` or `scoop install ffmpeg` |

## Repo

```
git clone https://github.com/YOUR_USERNAME/vidpost.git
cd vidpost
uv sync
```

## Per-machine state

vidpost stores per-machine state in `~/.vidpost/` (resolves to `C:\Users\<you>\.vidpost\` on Windows). This directory is **not** in the repo.

Files in `~/.vidpost/`:

| File | Per-machine? | Notes |
|------|--------------|-------|
| `config.yaml` | shared | API keys for FB/TikTok app, posting defaults. Two `_path` fields are absolute and need fixing per machine. |
| `style_guide.yaml` | shared | Caption tone and example reference |
| `youtube_client_secret.json` | shared | Google Cloud OAuth client config (same Google Cloud project, both machines) |
| `vidpost.db` | per-machine | Auth tokens, post history, scheduler queue, comment cursors. **Don't sync this across machines** — auth tokens shouldn't be shared, and post history references absolute video paths. |

### First-time setup on a new machine

1. Create the directory: `mkdir -p ~/.vidpost` (or `mkdir %USERPROFILE%\.vidpost` on Windows).
2. Copy `config.yaml`, `style_guide.yaml`, `youtube_client_secret.json` from your other machine into it.
3. **Important:** edit `config.yaml` so the two `_path` fields point at the new machine's home directory:
   ```yaml
   youtube:
     client_secret_path: <home>/.vidpost/youtube_client_secret.json
   captions:
     style_guide_path: <home>/.vidpost/style_guide.yaml
   ```
   On Windows, forward slashes are fine: `C:/Users/yourname/.vidpost/...`.
4. Authenticate each platform (creates a fresh local `vidpost.db`):
   ```
   uv run vidpost auth youtube
   uv run vidpost auth facebook
   uv run vidpost auth tiktok
   ```

## Videos and captions

vidpost expects videos in `~/Videos/reels/` with a `captions.txt` sidecar (see README for the format). The video files and captions.txt are not in the repo. Sync them separately across machines via rsync, cloud storage, or USB.

## YouTube auth: avoid the 7-day token expiry

After downloading `youtube_client_secret.json` from Google Cloud Console, make sure the OAuth consent screen for the project is set to **In production** (not Testing). Refresh tokens issued while the consent screen is in Testing mode expire after 7 days; in Production they're effectively permanent.

Settings → Google Auth Platform → Audience → click "Publish App".

## Troubleshooting

- **`Token has been expired or revoked`** on YouTube → re-run `uv run vidpost auth youtube`. If this happens roughly weekly, your Google OAuth consent screen is still in Testing mode (see above).
- **Facebook posts succeed but get poor reach** → confirm your Meta app at developers.facebook.com is set to **Mode: Live** (not In development). Dev-mode apps have severely limited distribution.
- **`vidpost: command not found`** → use `uv run vidpost ...` instead, or activate the venv with `source .venv/bin/activate` (Mac) / `.venv\Scripts\activate` (Windows).
