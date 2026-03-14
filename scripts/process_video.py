"""
process_video.py

Main entrypoint for the solo-grabber processing pipeline.

Usage:
  python process_video.py \\
    --video-url "https://www.youtube.com/watch?v=..." \\
    --reference-photo "./me.jpg" \\
    --person-id "ben_conn" \\
    --output-dir "./output" \\
    [--instrument saxophone] \\
    [--sample-rate 1.0] \\
    [--similarity-threshold 0.45] \\
    [--padding 5.0] \\
    [--skip-audio-analysis] \\
    [--skip-upload] \\
    [--no-demucs]

Outputs a JSON results file at <output_dir>/result.json.

Required env vars for upload (skip with --skip-upload):
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
  R2_PUBLIC_BASE_URL (optional)
"""

import argparse
import json
import logging
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from clip_extractor import extract_clips, get_video_duration
from face_detector import detect_segments
from audio_analyzer import analyze_clip

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def download_video(url: str, output_path: str) -> str:
    """
    Download a video from a URL using yt-dlp.
    Returns the path to the downloaded file.
    """
    try:
        import yt_dlp
    except ImportError:
        raise ImportError("yt-dlp not installed. Run: pip install yt-dlp")

    logger.info(f"Downloading video: {url}")

    # yt-dlp template — the actual extension depends on the format chosen
    outtmpl = output_path.rstrip(".mp4")

    ydl_opts = {
        "outtmpl": outtmpl + ".%(ext)s",
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "unknown")
        ext = info.get("ext", "mp4")

    downloaded = outtmpl + f".{ext}"
    if not Path(downloaded).exists():
        # yt-dlp may have merged to .mp4 regardless
        downloaded = outtmpl + ".mp4"

    logger.info(f"Downloaded: {downloaded} (title: {title!r})")
    return downloaded, title


def _validate_url(url: str) -> None:
    """
    Basic URL safety check. Rejects private/local addresses.
    The Go API layer should enforce an allowlist; this is a second line of defense.
    """
    import urllib.parse
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")

    hostname = parsed.hostname or ""
    blocked = ("localhost", "127.", "0.0.0.0", "::1", "169.254.", "10.", "192.168.", "172.")
    if any(hostname.startswith(b) for b in blocked):
        raise ValueError(f"Blocked hostname: {hostname!r}")


def process(args: argparse.Namespace) -> dict:
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    video_dir = out_dir / "video"
    video_dir.mkdir(exist_ok=True)
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    result = {
        "person_id": args.person_id,
        "instrument": args.instrument,
        "video_url": args.video_url,
        "video_title": None,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "clips": [],
        "errors": [],
    }

    # ── 1. Download ───────────────────────────────────────────────────────────
    _validate_url(args.video_url)
    video_path, video_title = download_video(
        args.video_url,
        str(video_dir / "source"),
    )
    result["video_title"] = video_title
    video_duration = get_video_duration(video_path)
    result["video_duration_seconds"] = round(video_duration, 2)

    # ── 2. Face detection ─────────────────────────────────────────────────────
    logger.info("Starting face detection...")
    segments = detect_segments(
        video_path=video_path,
        reference_photo=args.reference_photo,
        sample_rate=args.sample_rate,
        similarity_threshold=args.similarity_threshold,
    )

    if not segments:
        logger.warning("No segments found — person may not appear in this video.")
        result["errors"].append("No segments detected.")
        _save_result(result, out_dir)
        return result

    logger.info(f"Found {len(segments)} appearance segment(s).")

    # ── 3. Clip extraction ────────────────────────────────────────────────────
    clips = extract_clips(
        video_path=video_path,
        segments=segments,
        output_dir=str(clips_dir),
        padding=args.padding,
        video_duration=video_duration,
    )

    # ── 4. Audio analysis ─────────────────────────────────────────────────────
    for clip in clips:
        if not args.skip_audio_analysis:
            analysis_dir = str(clips_dir / f"analysis_{clip['clip_index']:03d}")
            try:
                clip["analysis"] = analyze_clip(
                    audio_path=clip["audio_path"],
                    output_dir=analysis_dir,
                    skip_demucs=args.no_demucs,
                )
            except Exception as e:
                logger.error(f"Audio analysis failed for clip {clip['clip_index']}: {e}")
                clip["analysis"] = {}
                result["errors"].append(f"clip_{clip['clip_index']:03d}: audio analysis failed — {e}")
        else:
            clip["analysis"] = {}

    # ── 5. Upload to R2 ───────────────────────────────────────────────────────
    if not args.skip_upload:
        from uploader import upload_clip
        for clip in clips:
            try:
                upload_clip(clip, args.person_id)
            except Exception as e:
                logger.error(f"Upload failed for clip {clip['clip_index']}: {e}")
                result["errors"].append(f"clip_{clip['clip_index']:03d}: upload failed — {e}")

    result["clips"] = clips
    _save_result(result, out_dir)
    return result


def _save_result(result: dict, out_dir: Path) -> None:
    result_path = out_dir / "result.json"
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info(f"Result written to: {result_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="solo-grabber video processing pipeline")

    parser.add_argument("--video-url", required=True, help="URL of the video to process")
    parser.add_argument("--reference-photo", required=True, help="Path to reference photo of target person")
    parser.add_argument("--person-id", required=True, help="Unique identifier for the person")
    parser.add_argument("--output-dir", default="./output", help="Directory for output files")
    parser.add_argument("--instrument", default="unknown", help="Instrument the person plays")

    # Detection tuning
    parser.add_argument("--sample-rate", type=float, default=1.0,
                        help="Frames per second to sample for face detection (default: 1.0)")
    parser.add_argument("--similarity-threshold", type=float, default=0.45,
                        help="Cosine similarity cutoff for face match (default: 0.45)")
    parser.add_argument("--padding", type=float, default=5.0,
                        help="Seconds of padding before/after each detected segment (default: 5.0)")

    # Flags
    parser.add_argument("--skip-audio-analysis", action="store_true",
                        help="Skip all audio analysis (faster, clips only)")
    parser.add_argument("--skip-upload", action="store_true",
                        help="Skip R2 upload (useful for local testing)")
    parser.add_argument("--no-demucs", action="store_true",
                        help="Skip Demucs source separation (faster, less accurate transcription)")

    args = parser.parse_args()

    try:
        result = process(args)
        clip_count = len(result.get("clips", []))
        error_count = len(result.get("errors", []))
        logger.info(f"Done. {clip_count} clip(s) extracted, {error_count} error(s).")
        if error_count:
            for e in result["errors"]:
                logger.warning(f"  - {e}")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
