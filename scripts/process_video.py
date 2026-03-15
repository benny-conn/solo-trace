"""
process_video.py

Main entrypoint for the solo-trace processing pipeline.

Usage:
  python process_video.py \\
    --video "https://www.youtube.com/watch?v=..." \\
    --reference-audio ./me.mov \\
    --person-id "ben_conn" \\
    --output-dir ./output \\
    [--model clap|mert] \\
    [--similarity-threshold 0.85] \\
    [--instrument trombone] \\
    [--padding 5.0] \\
    [--skip-audio-analysis] \\
    [--skip-clip-demucs] \\
    [--skip-upload]

--video accepts either a URL (YouTube etc.) or a local file path.

Outputs a JSON results file at <output_dir>/result.json.

Required env vars for upload (skip with --skip-upload):
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
  R2_PUBLIC_BASE_URL (optional)
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from clip_extractor import extract_clips, get_video_duration, Segment
from audio_embedder import detect_similar_segments, EMBEDDERS
from audio_analyzer import analyze_clip

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def _parse_start_time(s: str) -> float:
    """Parse a start time string into seconds. Accepts:
      - plain seconds: "3600", "3600.5"
      - MM:SS or HH:MM:SS: "1:00:00", "30:00"
    """
    s = s.strip()
    if re.match(r"^\d+(\.\d+)?$", s):
        return float(s)
    parts = s.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Unrecognised start-time format: {s!r}. Use seconds or HH:MM:SS.")


def _is_local_file(path: str) -> bool:
    """Return True if path points to an existing local file."""
    try:
        parsed = urllib.parse.urlparse(path)
        return parsed.scheme in ("", "file") or os.path.exists(path)
    except Exception:
        return False


def _validate_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    hostname = parsed.hostname or ""
    blocked = ("localhost", "127.", "0.0.0.0", "::1", "169.254.", "10.", "192.168.", "172.")
    if any(hostname.startswith(b) for b in blocked):
        raise ValueError(f"Blocked hostname: {hostname!r}")


def _get_video(video_arg: str, dest_dir: Path) -> tuple[str, str, str | None]:
    """
    Returns (video_path, title, upload_date).
    If video_arg is a local file, copies it to dest_dir and uses the filename as title.
    If it's a URL, downloads it with yt-dlp.
    """
    if _is_local_file(video_arg):
        src = Path(video_arg)
        dest = dest_dir / src.name
        if dest.resolve() != src.resolve():
            shutil.copy2(src, dest)
        logger.info(f"Using local file: {dest}")
        return str(dest), src.stem, None

    _validate_url(video_arg)
    return _download_video(video_arg, str(dest_dir / "source"))


def _download_video(url: str, output_path: str) -> tuple[str, str, str | None]:
    try:
        import yt_dlp
    except ImportError:
        raise ImportError("yt-dlp not installed. Run: pip install yt-dlp")

    logger.info(f"Downloading video: {url}")
    outtmpl = output_path.rstrip(".mp4")
    ydl_opts = {
        "outtmpl": outtmpl + ".%(ext)s",
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": False,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "unknown")
        ext = info.get("ext", "mp4")
        raw_date = info.get("upload_date")  # "YYYYMMDD" or None

    upload_date = None
    if raw_date and len(raw_date) == 8:
        upload_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"

    downloaded = outtmpl + f".{ext}"
    if not Path(downloaded).exists():
        downloaded = outtmpl + ".mp4"

    logger.info(f"Downloaded: {downloaded} (title: {title!r}, date: {upload_date})")
    return downloaded, title, upload_date


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
        "video": args.video,
        "video_title": None,
        "video_upload_date": None,
        "model": args.model,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "detection_params": {
            "audio_threshold": args.similarity_threshold,
            "min_peak": args.min_peak,
            "min_duration": 40.0,
            "gap_tolerance": 30.0,
            "start_time": args.start_time,
            "visual_check": args.visual_check,
            "visual_threshold": args.visual_threshold if args.visual_check else None,
        },
        "clips": [],
        "errors": [],
    }

    # ── 1. Get video ──────────────────────────────────────────────────────────
    video_path, video_title, video_upload_date = _get_video(args.video, video_dir)
    result["video_title"] = video_title
    result["video_upload_date"] = video_upload_date
    video_duration = get_video_duration(video_path)
    result["video_duration_seconds"] = round(video_duration, 2)

    start_time = _parse_start_time(args.start_time) if args.start_time else 0.0
    if start_time > 0:
        logger.info(f"Skipping first {start_time:.0f}s ({args.start_time}) of video")

    # ── 2. Extract full audio ─────────────────────────────────────────────────
    full_audio_path = str(out_dir / "full_audio.wav")
    if not Path(full_audio_path).exists():
        logger.info("Extracting full audio from video...")
        ss_args = ["-ss", str(start_time)] if start_time > 0 else []
        subprocess.run([
            FFMPEG, "-y", *ss_args, "-i", video_path,
            "-ac", "1", "-ar", "22050", "-vn",
            full_audio_path,
        ], check=True, capture_output=True)
    else:
        logger.info("Reusing existing full_audio.wav")

    # ── 3. Neural embedding similarity search ─────────────────────────────────
    logger.info(f"Loading embedder: {args.model}")
    embedder = EMBEDDERS[args.model]()
    embedder.load()

    # Re-extract audio at the embedder's required sample rate for the target
    target_audio_path = str(out_dir / f"full_audio_{embedder.sample_rate}.wav")
    if not Path(target_audio_path).exists():
        ss_args = ["-ss", str(start_time)] if start_time > 0 else []
        subprocess.run([
            FFMPEG, "-y", *ss_args, "-i", video_path,
            "-ac", "1", "-ar", str(embedder.sample_rate), "-vn",
            target_audio_path,
        ], check=True, capture_output=True)

    segments = detect_similar_segments(
        reference_path=args.reference_audio,
        target_path=target_audio_path,
        embedder=embedder,
        similarity_threshold=args.similarity_threshold,
        batch_size=args.batch_size,
        min_peak_score=args.min_peak,
        pitch_filter=args.pitch_filter,
        pitch_max_hz=args.pitch_max_hz,
        isolate_reference_stem=args.isolate_reference_stem,
    )
    logger.info(f"Found {len(segments)} segment(s) after audio detection.")

    if not segments:
        logger.warning("No segments found.")
        result["errors"].append("No segments detected.")
        _save_result(result, out_dir)
        return result

    # Embedder timestamps are relative to start_time; shift back to absolute video time
    # Keep audio metadata alongside each segment for result.json
    audio_meta = {i: s for i, s in enumerate(segments)}
    abs_segments = [Segment(start=s.start + start_time, end=s.end + start_time) for s in segments]
    visual_scores: dict[int, float] = {}  # segment index → visual score

    # ── 3b. Visual trombone check (optional second pass) ─────────────────────
    if args.visual_check:
        from visual_detector import TromboneVisualDetector
        logger.info("Running visual trombone detector on audio candidates...")
        vdet = TromboneVisualDetector()
        vdet.load(reference_images=args.reference_images or [])
        kept, vscores = vdet.filter_segments(
            video_path=video_path,
            segments=abs_segments,
            threshold=args.visual_threshold,
            n_frames=args.visual_frames,
        )
        # Map visual scores back to original segment indices
        for i, vs in enumerate(vscores):
            visual_scores[i] = vs.score
        abs_segments = kept
        logger.info(f"After visual filter: {len(abs_segments)} segment(s) remain.")
        if not abs_segments:
            logger.warning("Visual filter removed all segments.")
            result["errors"].append("No segments passed visual trombone check.")
            _save_result(result, out_dir)
            return result

    final_segments = abs_segments

    # ── 4. Clip extraction ────────────────────────────────────────────────────
    clips = extract_clips(
        video_path=video_path,
        segments=final_segments,
        output_dir=str(clips_dir),
        padding=args.padding,
        video_duration=video_duration,
    )

    # Attach detection metadata to each clip
    for clip in clips:
        idx = clip["clip_index"] - 1  # clip_index is 1-based
        seg = audio_meta.get(idx)
        clip["detection"] = {
            "audio_peak": round(seg.peak_score, 4) if seg else None,
            "audio_hit_count": seg.hit_count if seg else None,
            "audio_total_windows": seg.total_windows if seg else None,
            "audio_hit_ratio": round(seg.hit_ratio, 3) if seg else None,
            "visual_score": round(visual_scores[idx], 4) if idx in visual_scores else None,
        }

    # ── 5. Audio analysis ─────────────────────────────────────────────────────
    for clip in clips:
        if not args.skip_audio_analysis:
            analysis_dir = str(clips_dir / f"analysis_{clip['clip_index']:03d}")
            try:
                clip["analysis"] = analyze_clip(
                    audio_path=clip["audio_path"],
                    output_dir=analysis_dir,
                    skip_demucs=args.skip_clip_demucs,
                )
            except Exception as e:
                logger.error(f"Audio analysis failed for clip {clip['clip_index']}: {e}")
                clip["analysis"] = {}
                result["errors"].append(f"clip_{clip['clip_index']:03d}: audio analysis failed — {e}")
        else:
            clip["analysis"] = {}

    # ── 6. Upload to R2 ───────────────────────────────────────────────────────
    if not args.skip_upload:
        from uploader import upload_clip
        for clip in clips:
            try:
                upload_clip(clip, args.job_id)
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
    parser = argparse.ArgumentParser(description="solo-trace video processing pipeline")

    parser.add_argument("--video", required=True,
                        help="YouTube URL or local file path of the video to process")
    parser.add_argument("--reference-audio", required=True,
                        help="Audio/video file of you playing (e.g. me.mov). "
                             "Used to build the reference embedding.")
    parser.add_argument("--person-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--instrument", default="unknown")
    parser.add_argument("--start-time", default="0",
                        help="Skip this far into the video before processing. "
                             "Accepts seconds (\"3600\") or HH:MM:SS (\"1:00:00\"). "
                             "Useful to skip house band sets at the start of a recording.")

    # Model selection
    parser.add_argument("--model", default="clap", choices=["clap", "mert"],
                        help="Embedding model for similarity search (default: clap)")

    # Detection tuning
    parser.add_argument("--similarity-threshold", type=float, default=0.85,
                        help="Cosine similarity cutoff [0–1]. Start at 0.85 for CLAP, "
                             "0.80 for MERT. Tune with audio_embedder.py CLI.")
    parser.add_argument("--padding", type=float, default=5.0,
                        help="Seconds of padding around each segment (default: 5.0)")
    parser.add_argument("--batch-size", type=int, default=32,
                        help="Windows per embedding batch (default: 32)")
    parser.add_argument("--isolate-reference-stem", action="store_true",
                        help="Run Demucs on reference audio before embedding for a cleaner "
                             "instrument-focused signal. Only feasible on short reference clips.")
    parser.add_argument("--min-peak", type=float, default=0.0,
                        help="Discard audio segments whose peak window score is below this. "
                             "0.808 cleanly separates real solos from low-confidence brass matches.")
    parser.add_argument("--pitch-filter", action="store_true",
                        help="After CLAP matching, discard windows with no notes below "
                             "--pitch-max-hz. Weak on full-band mixes; better with isolated stems.")
    parser.add_argument("--pitch-max-hz", type=float, default=180.0)

    # Visual trombone detection (CLIP second pass)
    parser.add_argument("--visual-check", action="store_true",
                        help="Run CLIP visual detector on audio candidates to verify trombone is visible. "
                             "Requires PIL (pip install Pillow).")
    parser.add_argument("--reference-images", nargs="*", default=[],
                        help="Photos of you playing trombone. Used as additional positive CLIP embeddings "
                             "alongside text prompts. Improves accuracy in dark club lighting.")
    parser.add_argument("--visual-threshold", type=float, default=0.0,
                        help="CLIP visual score threshold. Score = trombone_similarity - other_similarity. "
                             "0.0 means trombone prompts must score at least as high as other-instrument prompts. "
                             "Calibrate with: python visual_detector.py --video ... --start ... --end ...")
    parser.add_argument("--visual-frames", type=int, default=6,
                        help="Frames to sample per segment for visual check (default: 6)")

    # Flags
    parser.add_argument("--skip-audio-analysis", action="store_true",
                        help="Skip per-clip BPM/key/transcription analysis")
    parser.add_argument("--skip-clip-demucs", action="store_true",
                        help="Skip Demucs in per-clip audio analysis")
    parser.add_argument("--skip-upload", action="store_true",
                        help="Skip R2 upload")

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
