"""
build_fingerprint.py

Builds a timbral fingerprint from a reference video/audio of you playing.
Run this once on me.mov (or any clean recording of yourself).

Steps:
  1. Extract audio from the video (FFmpeg)
  2. Run Demucs to isolate the "other" stem (your horn, away from drums/bass/piano)
  3. Compute MFCC + spectral statistics from the isolated stem
  4. Save the fingerprint to a JSON file

Usage:
  python build_fingerprint.py --input ./me.mov --output ./me_fingerprint.json
  python build_fingerprint.py --input ./me.mov --output ./me_fingerprint.json --no-demucs
"""

import argparse
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import librosa
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def extract_audio(input_path: str, output_wav: str) -> None:
    subprocess.run([
        FFMPEG, "-y", "-i", input_path,
        "-ac", "1", "-ar", "44100", "-vn",
        output_wav,
    ], check=True, capture_output=True)


def separate_other_stem(audio_path: str, stems_dir: str) -> str:
    """
    Run Demucs CLI and return path to the 'other' stem WAV.
    Stems are saved to stems_dir so you can listen to them.
    Default output layout: stems_dir/htdemucs/<track_name>/<stem>.wav
    """
    demucs = shutil.which("demucs") or "demucs"
    out = Path(stems_dir)
    out.mkdir(parents=True, exist_ok=True)

    # htdemucs_6s adds guitar + piano as separate stems, so "other" = horn only
    result = subprocess.run(
        [demucs, "--name", "htdemucs_6s", "--out", str(out), audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning(f"Demucs failed, using raw audio.\n{result.stderr}")
        return audio_path

    track_name = Path(audio_path).stem
    stem_dir = out / "htdemucs_6s" / track_name
    other_stem = stem_dir / "other.wav"
    vocals_stem = stem_dir / "vocals.wav"

    if not other_stem.exists():
        found = list(out.rglob("*.wav"))
        logger.warning(f"'other' stem not found. Found: {found}")
        logger.warning("Using raw audio.")
        return audio_path

    logger.info(f"Stems saved to: {stem_dir}/")

    # Demucs often puts brass/trombone in vocals due to spectral overlap with voice.
    # Mix other + vocals so we capture the full trombone signal regardless of which
    # stem it lands in. This will also be consistent when scanning the target video.
    if vocals_stem.exists():
        mixed_path = str(stem_dir / "horn_mixed.wav")
        mix_result = subprocess.run([
            FFMPEG, "-y",
            "-i", str(other_stem),
            "-i", str(vocals_stem),
            "-filter_complex", "amix=inputs=2:normalize=0",
            mixed_path,
        ], capture_output=True, text=True)

        if mix_result.returncode == 0:
            logger.info("Mixed other + vocals → horn_mixed.wav (captures trombone bleed into vocals stem)")
            return mixed_path
        else:
            logger.warning(f"Mix failed, using other.wav only.\n{mix_result.stderr}")

    return str(other_stem)


def compute_fingerprint(audio_path: str) -> dict:
    """
    Compute a timbral fingerprint from an audio file.

    Returns a dict with:
      - mfcc_mean: mean of per-frame MFCC vectors (shape: n_mfcc)
      - mfcc_std: std of per-frame MFCC vectors
      - mfcc_delta_mean: mean of MFCC delta (captures dynamics/articulation)
      - spectral_centroid_mean: brightness
      - spectral_rolloff_mean: high-frequency energy rolloff
      - zcr_mean: zero-crossing rate (captures breathiness/transients)
    """
    logger.info(f"Computing fingerprint from: {audio_path}")
    y, sr = librosa.load(audio_path, sr=22050, mono=True)

    n_mfcc = 20
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=n_mfcc)
    mfcc_delta = librosa.feature.delta(mfcc)

    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    zcr = librosa.feature.zero_crossing_rate(y)[0]

    return {
        "mfcc_mean": mfcc.mean(axis=1).tolist(),
        "mfcc_std": mfcc.std(axis=1).tolist(),
        "mfcc_delta_mean": mfcc_delta.mean(axis=1).tolist(),
        "spectral_centroid_mean": float(centroid.mean()),
        "spectral_rolloff_mean": float(rolloff.mean()),
        "zcr_mean": float(zcr.mean()),
    }


def build(input_path: str, output_path: str, skip_demucs: bool = False) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Stems go next to the output JSON so you can listen to them
    stems_dir = output_path.parent / (output_path.stem + "_stems")

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = str(Path(tmp) / "reference.wav")

        logger.info(f"Extracting audio from: {input_path}")
        extract_audio(input_path, wav_path)

        if skip_demucs:
            stem_path = wav_path
        else:
            logger.info("Running Demucs to isolate instrument stem...")
            stem_path = separate_other_stem(wav_path, str(stems_dir))

        fingerprint = compute_fingerprint(stem_path)
        fingerprint["source_file"] = str(Path(input_path).name)
        fingerprint["demucs_used"] = not skip_demucs

    with open(output_path, "w") as f:
        json.dump(fingerprint, f, indent=2)

    logger.info(f"Fingerprint saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Build timbral fingerprint from reference recording")
    parser.add_argument("--input", required=True, help="Path to reference video or audio (e.g. me.mov)")
    parser.add_argument("--output", default="./me_fingerprint.json", help="Output fingerprint JSON path")
    parser.add_argument("--no-demucs", action="store_true", help="Skip Demucs (use if recording is already a solo)")
    args = parser.parse_args()

    build(args.input, args.output, skip_demucs=args.no_demucs)


if __name__ == "__main__":
    main()
