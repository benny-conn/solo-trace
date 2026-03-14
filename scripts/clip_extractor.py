"""
clip_extractor.py

FFmpeg wrapper for extracting video clips and audio from a source video.
All time values are in seconds (float).
"""

import logging
import subprocess
import shutil
from pathlib import Path

from face_detector import Segment

logger = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def _run(cmd: list[str]) -> None:
    logger.debug(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{result.stderr}")


def extract_clips(
    video_path: str,
    segments: list[Segment],
    output_dir: str,
    padding: float = 5.0,       # seconds to add before/after each segment
    video_duration: float = 0,  # used to clamp end time; 0 = no clamping
) -> list[dict]:
    """
    Extract video clips for each segment with optional padding.

    Returns list of dicts:
        {
            "clip_index": int,
            "start": float,
            "end": float,
            "duration": float,
            "video_path": str,
            "audio_path": str,   # extracted WAV for audio analysis
        }
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    clips = []
    for i, seg in enumerate(segments):
        start = max(0.0, seg.start - padding)
        end = seg.end + padding
        if video_duration > 0:
            end = min(end, video_duration)
        duration = end - start

        video_out = str(out / f"clip_{i+1:03d}.mp4")
        audio_out = str(out / f"clip_{i+1:03d}.wav")

        logger.info(f"Extracting clip {i+1}/{len(segments)}: {start:.1f}s – {end:.1f}s")

        # Extract video clip (re-encode to ensure clean cut points)
        _run([
            FFMPEG, "-y",
            "-ss", str(start),
            "-i", video_path,
            "-t", str(duration),
            "-c:v", "libx264", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            video_out,
        ])

        # Extract audio as WAV for analysis tools (mono, 44100 Hz)
        _run([
            FFMPEG, "-y",
            "-ss", str(start),
            "-i", video_path,
            "-t", str(duration),
            "-ac", "1",
            "-ar", "44100",
            "-vn",
            audio_out,
        ])

        clips.append({
            "clip_index": i + 1,
            "start": start,
            "end": end,
            "duration": duration,
            "video_path": video_out,
            "audio_path": audio_out,
        })

    return clips


def get_video_duration(video_path: str) -> float:
    """Return video duration in seconds using ffprobe."""
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    result = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("Could not determine video duration via ffprobe.")
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0
