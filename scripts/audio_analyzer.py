"""
audio_analyzer.py

Audio analysis pipeline for a single clip:
  1. Demucs — source separation, isolate the "other" stem (horn/melodic instruments)
  2. Madmom — beat tracking & BPM (handles swing)
  3. basic-pitch — pitch detection & MIDI transcription
  4. librosa — key estimation, energy, spectral features

All functions take a WAV file path and return dicts that are safe to
serialize to JSON (no numpy types).
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Krumhansl-Schmuckler key profiles
_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def separate_stems(audio_path: str, output_dir: str) -> dict[str, str]:
    """
    Run Demucs CLI and return paths to separated stems.
    Requires torchcodec: pip install torchcodec
    """
    demucs = shutil.which("demucs") or "demucs"
    out = Path(output_dir) / "demucs"

    logger.info("Running Demucs source separation (this may take a while)...")
    # htdemucs_6s separates guitar + piano, leaving "other" = horn only
    result = subprocess.run(
        [demucs, "--name", "htdemucs_6s", "--out", str(out), audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning(f"Demucs failed, falling back to original audio.\n{result.stderr}")
        return {"other": audio_path}

    track_name = Path(audio_path).stem
    stem_dir = out / "htdemucs_6s" / track_name
    stems = {f.stem: str(f) for f in stem_dir.glob("*.wav")}

    if "other" not in stems:
        logger.warning("Demucs 'other' stem not found, using original audio.")
        stems["other"] = audio_path
        return stems

    # Mix other + vocals to capture brass bleed (trombone often lands in vocals)
    if "vocals" in stems:
        mixed_path = str(stem_dir / "horn_mixed.wav")
        mix_result = subprocess.run([
            shutil.which("ffmpeg") or "ffmpeg", "-y",
            "-i", stems["other"],
            "-i", stems["vocals"],
            "-filter_complex", "amix=inputs=2:normalize=0",
            mixed_path,
        ], capture_output=True, text=True)

        if mix_result.returncode == 0:
            stems["other"] = mixed_path
            logger.info("Mixed other + vocals for horn_mixed stem")

    logger.info(f"Demucs stems: {list(stems.keys())}")
    return stems


def detect_beats(audio_path: str) -> dict:
    """
    Use Madmom DBN beat tracker to detect beats and estimate BPM.
    Returns {"bpm": float, "beat_times": list[float]}
    """
    try:
        import madmom
    except ImportError:
        logger.warning("madmom not installed, skipping beat detection.")
        return {"bpm": None, "beat_times": []}

    logger.info("Running Madmom beat detection...")
    try:
        proc = madmom.features.beats.DBNBeatTrackingProcessor(fps=100)
        act = madmom.features.beats.RNNBeatProcessor()(audio_path)
        beat_times = proc(act).tolist()

        if len(beat_times) >= 2:
            intervals = np.diff(beat_times)
            median_interval = float(np.median(intervals))
            bpm = round(60.0 / median_interval, 2) if median_interval > 0 else None
        else:
            bpm = None

        logger.info(f"BPM: {bpm}, beats detected: {len(beat_times)}")
        return {"bpm": bpm, "beat_times": beat_times}
    except Exception as e:
        logger.warning(f"Beat detection failed: {e}")
        return {"bpm": None, "beat_times": []}


def transcribe_pitch(audio_path: str, output_dir: str) -> dict:
    """
    Run Spotify basic-pitch to get MIDI transcription and note events.
    Returns {"midi_path": str | None, "note_events": list[dict]}
    """
    try:
        from basic_pitch.inference import predict
        from basic_pitch import ICASSP_2022_MODEL_PATH
    except ImportError:
        logger.warning("basic-pitch not installed, skipping transcription.")
        return {"midi_path": None, "note_events": []}

    logger.info("Running basic-pitch transcription...")
    try:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        model_output, midi_data, note_events = predict(
            audio_path,
            ICASSP_2022_MODEL_PATH,
        )

        midi_path = str(out / (Path(audio_path).stem + ".mid"))
        midi_data.write(midi_path)

        # Serialize note events to plain dicts
        events = [
            {
                "start_time": float(n.start_time),
                "end_time": float(n.end_time),
                "pitch_midi": int(n.pitch),
                "amplitude": float(n.amplitude),
            }
            for n in note_events
        ]

        logger.info(f"Transcribed {len(events)} note events. MIDI: {midi_path}")
        return {"midi_path": midi_path, "note_events": events}
    except Exception as e:
        logger.warning(f"Pitch transcription failed: {e}")
        return {"midi_path": None, "note_events": []}


def extract_features(audio_path: str) -> dict:
    """
    Use librosa to extract key, energy, and spectral features.
    Returns a JSON-serializable dict.
    """
    try:
        import librosa
    except ImportError:
        logger.warning("librosa not installed, skipping feature extraction.")
        return {}

    logger.info("Extracting audio features with librosa...")
    try:
        y, sr = librosa.load(audio_path, sr=None, mono=True)

        # Key estimation via Krumhansl-Schmuckler
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = chroma.mean(axis=1)  # 12-dim vector
        key_name, mode = _estimate_key(chroma_mean)

        # Energy (RMS)
        rms = librosa.feature.rms(y=y)[0]
        energy_mean = float(np.mean(rms))
        energy_max = float(np.max(rms))

        # Spectral centroid (brightness)
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        spectral_centroid_mean = float(np.mean(centroid))

        # Onset strength (rhythmic density)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        onset_strength_mean = float(np.mean(onset_env))

        # Note histogram from chroma (rough pitch class distribution)
        note_histogram = {
            _NOTE_NAMES[i]: round(float(chroma_mean[i]), 4)
            for i in range(12)
        }

        return {
            "key": key_name,
            "mode": mode,
            "energy_mean": round(energy_mean, 4),
            "energy_max": round(energy_max, 4),
            "spectral_centroid_mean": round(spectral_centroid_mean, 2),
            "onset_strength_mean": round(onset_strength_mean, 4),
            "note_histogram": note_histogram,
        }
    except Exception as e:
        logger.warning(f"Feature extraction failed: {e}")
        return {}


def analyze_clip(audio_path: str, output_dir: str, skip_demucs: bool = False) -> dict:
    """
    Full analysis pipeline for a single clip audio file.

    Pipeline:
      1. Demucs separation (isolate horn/melody into "other" stem)
      2. Beat tracking on original mix (full band gives better rhythm info)
      3. Pitch transcription on isolated stem
      4. Feature extraction on isolated stem

    Returns a merged dict ready for JSON serialization.
    """
    analysis: dict = {}

    # Step 1: Source separation
    if skip_demucs:
        stem_audio = audio_path
    else:
        stems = separate_stems(audio_path, output_dir)
        stem_audio = stems.get("other", audio_path)

    # Step 2: Beat tracking (on original mix — full band is more reliable)
    beat_result = detect_beats(audio_path)
    analysis.update(beat_result)

    # Step 3: Pitch transcription on isolated stem
    transcription_dir = str(Path(output_dir) / "midi")
    pitch_result = transcribe_pitch(stem_audio, transcription_dir)
    analysis.update(pitch_result)

    # Step 4: Spectral features on isolated stem
    features = extract_features(stem_audio)
    analysis.update(features)

    return analysis


# ── Key estimation helpers ────────────────────────────────────────────────────

def _estimate_key(chroma_mean: np.ndarray) -> tuple[str, str]:
    """
    Estimate musical key using Krumhansl-Schmuckler profiles.
    Returns (note_name, "major" | "minor").
    """
    best_key = 0
    best_mode = "major"
    best_score = -np.inf

    for i in range(12):
        rotated = np.roll(chroma_mean, -i)

        major_score = float(np.corrcoef(rotated, _KS_MAJOR)[0, 1])
        if major_score > best_score:
            best_score = major_score
            best_key = i
            best_mode = "major"

        minor_score = float(np.corrcoef(rotated, _KS_MINOR)[0, 1])
        if minor_score > best_score:
            best_score = minor_score
            best_key = i
            best_mode = "minor"

    return _NOTE_NAMES[best_key], best_mode
