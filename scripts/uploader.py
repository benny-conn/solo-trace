"""
uploader.py

Cloudflare R2 upload client (S3-compatible via boto3).

Required environment variables:
  R2_ACCOUNT_ID       — Cloudflare account ID
  R2_ACCESS_KEY_ID    — R2 access key
  R2_SECRET_ACCESS_KEY — R2 secret key
  R2_BUCKET           — Bucket name
  R2_PUBLIC_BASE_URL  — Optional public base URL (e.g. https://clips.example.com)
                        If set, returned URLs use this base instead of the R2 endpoint.
"""

import logging
import mimetypes
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_client():
    try:
        import boto3
    except ImportError:
        raise ImportError("boto3 not installed. Run: pip install boto3")

    account_id = os.environ["R2_ACCOUNT_ID"]
    access_key = os.environ["R2_ACCESS_KEY_ID"]
    secret_key = os.environ["R2_SECRET_ACCESS_KEY"]

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def upload_file(local_path: str, r2_key: str) -> str:
    """
    Upload a file to R2 and return its public URL.

    Args:
        local_path: Local file path.
        r2_key: Destination key in R2 (e.g. "clips/person_id/clip_001.mp4").

    Returns:
        Public URL string.
    """
    bucket = os.environ["R2_BUCKET"]
    client = _get_client()

    content_type, _ = mimetypes.guess_type(local_path)
    extra_args = {}
    if content_type:
        extra_args["ContentType"] = content_type

    logger.info(f"Uploading {local_path} → r2://{bucket}/{r2_key}")
    client.upload_file(local_path, bucket, r2_key, ExtraArgs=extra_args)

    base_url = os.environ.get("R2_PUBLIC_BASE_URL", "").rstrip("/")
    if base_url:
        return f"{base_url}/{r2_key}"

    # Fallback: construct R2 URL directly (requires bucket to be public)
    account_id = os.environ["R2_ACCOUNT_ID"]
    return f"https://{account_id}.r2.cloudflarestorage.com/{bucket}/{r2_key}"


def upload_clip(clip: dict, person_id: str) -> dict:
    """
    Upload a clip's video and MIDI files to R2.
    Mutates the clip dict in place, adding "r2_video_key", "r2_video_url",
    and optionally "r2_midi_key", "r2_midi_url".

    Returns the updated clip dict.
    """
    clip_index = clip["clip_index"]
    base_key = f"clips/{person_id}/clip_{clip_index:03d}"

    # Upload video
    video_key = f"{base_key}.mp4"
    clip["r2_video_key"] = video_key
    clip["r2_video_url"] = upload_file(clip["video_path"], video_key)

    # Upload MIDI if it exists
    midi_path = clip.get("analysis", {}).get("midi_path")
    if midi_path and Path(midi_path).exists():
        midi_key = f"{base_key}.mid"
        clip["analysis"]["r2_midi_key"] = midi_key
        clip["analysis"]["r2_midi_url"] = upload_file(midi_path, midi_key)

    return clip
