"""Video analysis: audio transcription via Whisper + keyframe extraction via ffmpeg."""

import subprocess
import tempfile
from pathlib import Path

from vidpost.config import load_config
from vidpost.models import CaptionAnalysis


def extract_audio(video_path: Path, output_path: Path | None = None) -> Path:
    """Extract audio track from video using ffmpeg."""
    if output_path is None:
        output_path = video_path.with_suffix(".wav")

    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn",                    # no video
        "-acodec", "pcm_s16le",   # WAV format for Whisper
        "-ar", "16000",           # 16kHz sample rate (Whisper expects this)
        "-ac", "1",               # mono
        "-y",                     # overwrite
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr}")
    return output_path


def extract_keyframes(video_path: Path, num_frames: int = 5, output_dir: Path | None = None) -> list[Path]:
    """Extract evenly-spaced keyframes from video using ffmpeg."""
    if output_dir is None:
        output_dir = video_path.parent / f".{video_path.stem}_frames"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get video duration first
    duration = get_video_duration(video_path)
    if duration <= 0:
        raise RuntimeError(f"Could not determine duration of {video_path}")

    # Calculate timestamps for evenly-spaced frames
    interval = duration / (num_frames + 1)
    frames = []

    for i in range(1, num_frames + 1):
        timestamp = interval * i
        output_path = output_dir / f"frame_{i:02d}.jpg"
        cmd = [
            "ffmpeg", "-ss", str(timestamp),
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",  # high quality JPEG
            "-y",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and output_path.exists():
            frames.append(output_path)

    return frames


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def transcribe_audio(audio_path: Path, model_name: str | None = None) -> str:
    """Transcribe audio using faster-whisper (runs locally, free)."""
    if model_name is None:
        config = load_config()
        model_name = config.get("captions", {}).get("whisper_model", "base")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError(
            "faster-whisper not installed. Install it:\n"
            "  pip install faster-whisper\n"
            "First run will download the model (~140MB for 'base')."
        )

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(audio_path), beam_size=5)

    transcript_parts = []
    for segment in segments:
        transcript_parts.append(segment.text.strip())

    return " ".join(transcript_parts)


def analyze_video(
    video_path: Path,
    num_keyframes: int | None = None,
    skip_audio: bool = False,
    context: str = "",
) -> CaptionAnalysis:
    """Full video analysis pipeline: extract audio, transcribe, extract keyframes.

    Returns a CaptionAnalysis with transcript and keyframe paths.
    The user then asks Claude Code to generate captions from this analysis.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    config = load_config()
    if num_keyframes is None:
        num_keyframes = config.get("captions", {}).get("num_keyframes", 5)

    duration = get_video_duration(video_path)
    transcript = None

    # Step 1: Extract and transcribe audio
    if not skip_audio:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.wav"
            try:
                extract_audio(video_path, audio_path)
                if audio_path.stat().st_size > 1000:  # Skip near-empty audio
                    transcript = transcribe_audio(audio_path)
                    if transcript and len(transcript.strip()) < 10:
                        transcript = None  # Too short to be useful
            except Exception:
                pass  # Audio extraction failed — likely no audio track

    # Step 2: Extract keyframes
    keyframes = extract_keyframes(video_path, num_frames=num_keyframes)

    return CaptionAnalysis(
        video_path=str(video_path),
        transcript=transcript,
        keyframe_paths=[str(p) for p in keyframes],
        duration_seconds=duration,
        context=context,
    )
