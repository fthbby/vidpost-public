# vidpost

CLI tool for posting videos to YouTube, Facebook, and TikTok. Designed to be driven conversationally with Claude Code.

## What this can do

- Post videos to YouTube, Facebook (multiple pages), and TikTok from the command line
- Post to multiple platforms at once with per-platform caption transforms (e.g. clean Instagram-style captions for Facebook/YouTube)
- Batch-post a whole folder of videos, optionally spaced out on a schedule
- Schedule posts for a future time and run a background daemon to publish them
- Retry failed posts and manage/cancel scheduled ones
- Load captions from a `caption.txt` sidecar file, or pass `--caption` inline
- Analyze videos locally (faster-whisper transcript + ffmpeg keyframes) to help Claude Code generate captions
- Swap the audio track on a video (`vidpost swap-audio`)
- Check post history and status per post ID
- Auto-detect Shorts (vertical, <60s) for YouTube
- Dry-run mode to preview any post or batch before it goes live

## Setup

```bash
# Install (requires Python 3.11+)
cd vidpost
uv sync
source .venv/bin/activate

# Initialize config files
vidpost init
```

This creates `~/.vidpost/config.yaml` and `~/.vidpost/style_guide.yaml`.

### Platform Setup

#### YouTube
1. Create a Google Cloud project at https://console.cloud.google.com
2. Enable **YouTube Data API v3**
3. Go to **APIs & Services > OAuth consent screen**, create External app, add your Gmail as a test user
4. Go to **Credentials > Create Credentials > OAuth client ID** (Desktop app)
5. Download the JSON file:
   ```bash
   mv ~/Downloads/client_secret_*.json ~/.vidpost/youtube_client_secret.json
   ```
6. Authenticate:
   ```bash
   vidpost auth youtube
   ```
   Opens a URL — sign in with your Google account in Safari.

**Limits:** ~6 uploads/day on free tier. Videos under 60s with vertical aspect ratio automatically become Shorts.

#### Facebook
1. Create an app at https://developers.facebook.com (Business type)
2. Go to https://developers.facebook.com/tools/explorer
3. Select your app, add permissions: `pages_show_list`, `pages_manage_posts`, `pages_read_engagement`
4. Click **Generate Access Token**, authorize, select your Pages
5. The token is saved automatically when you run:
   ```bash
   vidpost auth facebook
   ```

**Multiple pages:** Post to different pages using `facebook:pagename` syntax:
```bash
vidpost post video.mp4 --platforms facebook,facebook:secondary
```

**Limits:** Max 10GB, 240 minutes. No strict daily upload quota.

#### TikTok
1. Create an app at https://developers.tiktok.com
2. Add **Content Posting API** product
3. Submit for review (takes days to weeks)
4. Add client key and secret to `~/.vidpost/config.yaml`
5. After approval:
   ```bash
   vidpost auth tiktok
   ```

**Limits:** Max 1GB, 60 minutes, min 3 seconds. MP4 or WebM only. ~15-20 posts/day.

## Usage

### Post a video
```bash
# Post to YouTube
vidpost post video.mp4 --platforms youtube

# Post to multiple platforms
vidpost post video.mp4 --platforms youtube,facebook,tiktok

# Post to both Facebook pages
vidpost post video.mp4 --platforms facebook,facebook:secondary

# With a caption override
vidpost post video.mp4 --caption "Your caption here" --platforms youtube

# Schedule for later
vidpost post video.mp4 --platforms youtube --schedule "2026-04-14 10:00"

# Dry run (see what would happen)
vidpost post video.mp4 --platforms youtube --dry-run
```

### Captions

Captions are loaded from a `caption.txt` (or `captions.txt`) file in the same folder as the video. Format:

```
[video-name.mp4]
Your caption here
Can be multiple lines
#hashtag1 #hashtag2

[another-video.mp4]
Another caption
#food #socal
```

If no caption file exists, you can:
- Pass `--caption "..."` on the command line
- Run `vidpost caption video.mp4` to extract audio/keyframes, then ask Claude Code to write a caption

### Analyze a video for caption generation
```bash
# Extract transcript + keyframes (all local, free)
vidpost caption video.mp4

# Add context to help with caption generation
vidpost caption video.mp4 --context "what the video is about (setting, subject, vibe)"

# Skip audio (for b-roll with just music)
vidpost caption video.mp4 --no-audio
```

This uses faster-whisper (local, free) for audio transcription and ffmpeg for keyframe extraction. Then ask Claude Code to generate captions based on the output.

### Batch post a folder
```bash
# Post everything in a folder
vidpost batch ./videos/this-week/ --platforms youtube,facebook

# Schedule posts spaced out every 2 days
vidpost batch ./videos/this-week/ --schedule-start "2026-04-14 11:00" --schedule-interval 2d

# Generate empty YAML sidecars for all videos
vidpost batch ./videos/this-week/ --generate-metadata

# Preview what would happen
vidpost batch ./videos/this-week/ --dry-run
```

### Check status
```bash
vidpost status              # Recent post history
vidpost status <post-id>    # Details for one post
```

### Manage scheduled posts
```bash
vidpost schedule list
vidpost schedule cancel <post-id>
vidpost schedule retry <post-id>
```

### Background scheduler
```bash
vidpost daemon start    # Start scheduler daemon
vidpost daemon stop     # Stop it
vidpost daemon status   # Check if running
```

## Config

Config lives at `~/.vidpost/config.yaml`. Key settings:

```yaml
youtube:
  default_privacy: public     # public | unlisted | private

defaults:
  platforms:                   # Default platforms when --platforms is omitted
    - youtube
    - facebook
    - tiktok
  timezone: America/Los_Angeles

captions:
  whisper_model: base          # tiny | base | small | medium | large
  num_keyframes: 5
```

## Style Guide

Edit `~/.vidpost/style_guide.yaml` with 15-20 of your best caption examples. This is used as reference when Claude Code generates captions for you.

## Requirements

- Python 3.11+
- ffmpeg (`brew install ffmpeg`)
- faster-whisper downloads its model on first use (~140MB for `base`)
