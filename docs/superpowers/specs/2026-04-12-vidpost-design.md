# VidPost Design Spec

## Overview
Python CLI tool for posting videos to YouTube, Facebook, and TikTok with AI-powered caption generation. Designed for conversational use with Claude Code.

## Architecture Decisions
- **faster-whisper** over openai-whisper (10x faster, smaller footprint, same accuracy)
- **Click** CLI with subcommands: `init`, `post`, `caption`, `batch`, `schedule`, `status`, `daemon`
- **SQLite** for post queue/history + auth token storage
- **APScheduler** for background scheduled posting
- **httpx async** for parallel platform uploads
- **rich** for terminal UI (progress bars, tables, colored output)
- Platform integrations are stubbed — user will fill in real API calls later with auth credentials

## Scope
All 7 phases built. Platform API calls (YouTube, Facebook, TikTok) will have working OAuth flows and upload methods structured correctly but designed so the user can plug in real credentials and test incrementally.

## Key Flows
1. `vidpost caption video.mp4` → extract audio → whisper transcribe → extract keyframes → Claude vision analyze → generate 3 caption options → user picks → save to YAML sidecar
2. `vidpost post video.mp4 --caption "..." --platforms youtube,tiktok` → create DB record → upload to each platform → update status
3. `vidpost batch ./folder/` → scan for videos → generate missing captions → queue all → upload/schedule
4. `vidpost daemon start` → APScheduler background process → fires scheduled posts at their times

## Data Model
- Posts table: tracks each video×platform combination through pending→scheduled→uploading→posted→failed
- Auth tokens table: per-platform OAuth tokens with refresh capability
- YAML sidecars: optional per-video metadata files for batch workflows
- Config YAML: app credentials + defaults at ~/.vidpost/config.yaml

## Caption Generation Pipeline
1. ffmpeg extracts audio track → faster-whisper transcribes locally
2. ffmpeg extracts 4-6 keyframes → sent to Claude vision API
3. Style guide loaded from ~/.vidpost/style_guide.yaml (few-shot examples)
4. Claude API generates 3 caption options matching user's voice
5. Interactive terminal picker → selected caption saved to YAML sidecar
