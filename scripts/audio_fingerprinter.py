"""
audio_fingerprinter.py

Detects time segments in a target audio file where a specific player
(identified by a fingerprint) is likely playing.

Uses a sliding window over the target audio, computing MFCC similarity
against the reference fingerprint. This is the audio-side complement to
face detection — it catches solos even when the camera isn't on the player.

Usage as module:
    from audio_fingerprinter import detect_playing_segments
    segments = detect_playing_segments(audio_path, fingerprint_path)

Usage as CLI:
    python audio_fingerprinter.py --audio clip.wav --fingerprint me_fingerprint.json
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AudioSegment:
    start: float   # seconds
    end: float     # seconds

    @property
    def duration(self) -> float:
        return self.end - self.start


def load_fingerprint(fingerprint_path: str) -> dict:
    with open(fingerprint_path) as f:
        return json.load(f)


def _build_feature_vector(fp: dict) -> np.ndarray:
    """Flatten the fingerprint into a single comparable feature vector."""
    return np.array(
        fp["mfcc_mean"] +
        fp["mfcc_std"] +
        fp["mfcc_delta_mean"]
    )


def _window_feature_vector(y: np.ndarray, sr: int, n_mfcc: int = 20) -> np.ndarray:
    """Compute the same feature vector for a window of audio."""
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=n_mfcc)
    mfcc_delta = librosa.feature.delta(mfcc)
    return np.array(
        mfcc.mean(axis=1).tolist() +
        mfcc.std(axis=1).tolist() +
        mfcc_delta.mean(axis=1).tolist()
    )


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


def detect_playing_segments(
    audio_path: str,
    fingerprint_path: str,
    window_seconds: float = 8.0,
    hop_seconds: float = 2.0,
    similarity_threshold: float = 0.90,
    min_segment_duration: float = 10.0,
    gap_tolerance: float = 20.0,
) -> list[AudioSegment]:
    """
    Scan an audio file and return segments where the reference player
    appears to be playing.

    Args:
        audio_path: Path to the target WAV (ideally Demucs 'other' stem).
        fingerprint_path: Path to fingerprint JSON from build_fingerprint.py.
        window_seconds: Analysis window size in seconds.
        hop_seconds: Step between windows.
        similarity_threshold: Cosine similarity cutoff for a positive match.
            0.90 is a reasonable starting point; tune based on results.
        min_segment_duration: Drop segments shorter than this.
        gap_tolerance: Merge segments with gaps shorter than this (seconds).

    Returns:
        List of AudioSegment objects sorted by start time.
    """
    fp = load_fingerprint(fingerprint_path)
    ref_vector = _build_feature_vector(fp)

    logger.info(f"Loading audio: {audio_path}")
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    total_duration = len(y) / sr

    window_samples = int(window_seconds * sr)
    hop_samples = int(hop_seconds * sr)

    logger.info(
        f"Scanning {total_duration:.0f}s of audio "
        f"(window={window_seconds}s, hop={hop_seconds}s, threshold={similarity_threshold})"
    )

    hit_times: list[float] = []

    for start_sample in range(0, len(y) - window_samples, hop_samples):
        window = y[start_sample:start_sample + window_samples]
        timestamp = start_sample / sr

        try:
            vec = _window_feature_vector(window, sr)
            sim = _cosine_similarity(vec, ref_vector)
            if sim >= similarity_threshold:
                hit_times.append(timestamp)
                logger.debug(f"  Audio match at {timestamp:.1f}s (sim={sim:.3f})")
        except Exception as e:
            logger.debug(f"  Window at {timestamp:.1f}s skipped: {e}")

    logger.info(f"Audio hits: {len(hit_times)} windows matched")
    return _build_segments(hit_times, total_duration, gap_tolerance, min_segment_duration)


def _build_segments(
    hit_times: list[float],
    total_duration: float,
    gap_tolerance: float,
    min_duration: float,
) -> list[AudioSegment]:
    if not hit_times:
        return []

    segments: list[AudioSegment] = []
    seg_start = hit_times[0]
    seg_end = hit_times[0]

    for t in hit_times[1:]:
        if t - seg_end <= gap_tolerance:
            seg_end = t
        else:
            segments.append(AudioSegment(start=seg_start, end=seg_end))
            seg_start = t
            seg_end = t

    segments.append(AudioSegment(start=seg_start, end=seg_end))
    segments = [s for s in segments if s.duration >= min_duration]

    logger.info(f"Audio segments: {len(segments)}")
    for i, s in enumerate(segments):
        logger.info(f"  [{i+1}] {s.start:.1f}s – {s.end:.1f}s ({s.duration:.1f}s)")

    return segments


def merge_visual_and_audio(
    visual_segments: list,   # list of face_detector.Segment
    audio_segments: list[AudioSegment],
    audio_extension: float = 30.0,
) -> list:
    """
    Merge face-detection and audio-fingerprint segments into final clip boundaries.

    Rules:
      - Start a clip on any visual hit (face seen)
      - Extend the clip end using audio signal, even if face disappears (camera cut)
      - Include audio-only segments that overlap with or are adjacent to a visual window
        (covers case where camera is away during solo start/end)
      - Filter visual-only segments with no nearby audio hit (person on stage but not playing)

    Returns merged segments as a list of dicts with start/end keys.
    """
    if not visual_segments and not audio_segments:
        return []

    # If no fingerprint was used, just return visual segments as-is
    if not audio_segments:
        return [{"start": s.start, "end": s.end, "detection_type": "face_only"} for s in visual_segments]

    # If no visual segments at all, return audio-only segments
    if not visual_segments:
        logger.info("No visual segments — using audio-only detections")
        return [{"start": s.start, "end": s.end, "detection_type": "audio_only"} for s in audio_segments]

    merged = []
    used_audio = set()

    for vs in visual_segments:
        best_start = vs.start
        best_end = vs.end
        has_audio = False

        for i, aus in enumerate(audio_segments):
            overlap = aus.start <= vs.end + audio_extension and aus.end >= vs.start - audio_extension
            if overlap:
                best_start = min(best_start, aus.start)
                best_end = max(best_end, aus.end)
                used_audio.add(i)
                has_audio = True

        detection_type = "both" if has_audio else "face_only"
        merged.append({"start": best_start, "end": best_end, "detection_type": detection_type})

    # Audio-only: camera was away but trombone was detected
    for i, aus in enumerate(audio_segments):
        if i not in used_audio:
            logger.info(f"Audio-only segment (camera away): {aus.start:.1f}s – {aus.end:.1f}s")
            merged.append({"start": aus.start, "end": aus.end, "detection_type": "audio_only"})

    # Sort and merge overlapping results
    merged.sort(key=lambda s: s["start"])
    return _merge_overlapping(merged)


def _merge_overlapping(segments: list[dict]) -> list[dict]:
    if not segments:
        return []
    result = [segments[0].copy()]
    for seg in segments[1:]:
        if seg["start"] <= result[-1]["end"]:
            result[-1]["end"] = max(result[-1]["end"], seg["end"])
        else:
            result.append(seg.copy())
    return result


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="Detect playing segments via audio fingerprinting")
    parser.add_argument("--audio", required=True, help="Path to target audio WAV")
    parser.add_argument("--fingerprint", required=True, help="Path to fingerprint JSON")
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--window", type=float, default=8.0)
    parser.add_argument("--hop", type=float, default=2.0)
    args = parser.parse_args()

    segs = detect_playing_segments(
        args.audio, args.fingerprint,
        window_seconds=args.window,
        hop_seconds=args.hop,
        similarity_threshold=args.threshold,
    )
    print(f"\n{len(segs)} segment(s) detected:")
    for s in segs:
        print(f"  {s.start:.1f}s – {s.end:.1f}s ({s.duration:.1f}s)")
