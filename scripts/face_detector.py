"""
face_detector.py

Detects a target person in a video using DeepFace (ArcFace model).
Samples frames at a configurable rate and returns time segments where the
person appears, with short gaps merged so each segment represents a continuous
"appearance".
"""

import cv2
import numpy as np
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Segment:
    start: float  # seconds
    end: float    # seconds

    @property
    def duration(self) -> float:
        return self.end - self.start


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _get_reference_embedding(reference_photo: str) -> np.ndarray:
    from deepface import DeepFace

    result = DeepFace.represent(
        img_path=reference_photo,
        model_name="ArcFace",
        enforce_detection=True,
        detector_backend="retinaface",
    )
    if not result:
        raise ValueError("No face detected in reference photo.")
    if len(result) > 1:
        logger.warning("Multiple faces in reference photo — using the first (largest) detected face.")

    embedding = np.array(result[0]["embedding"])
    return embedding / np.linalg.norm(embedding)  # L2 normalize


def detect_segments(
    video_path: str,
    reference_photo: str,
    sample_rate: float = 1.0,
    similarity_threshold: float = 0.45,
    min_segment_duration: float = 10.0,
    gap_tolerance: float = 30.0,
) -> list[Segment]:
    """
    Scan a video and return time segments where the target person appears.

    Args:
        video_path: Path to downloaded video file.
        reference_photo: Path to a clear photo of the target person.
        sample_rate: Frames per second to sample (lower = faster).
        similarity_threshold: Cosine similarity cutoff for a positive match.
        min_segment_duration: Drop segments shorter than this (spurious hits).
        gap_tolerance: Merge adjacent segments separated by less than this (seconds).

    Returns:
        List of Segment objects sorted by start time.
    """
    try:
        from deepface import DeepFace
    except ImportError:
        raise ImportError("deepface not installed. Run: pip install deepface")

    logger.info("Generating ArcFace embedding from reference photo...")
    reference_embedding = _get_reference_embedding(reference_photo)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    total_duration = total_frames / fps
    frame_interval = max(1, int(fps / sample_rate))

    logger.info(
        f"Video: {total_duration:.0f}s ({total_frames} frames at {fps:.1f}fps). "
        f"Sampling every {frame_interval} frames (~{sample_rate}fps)."
    )

    hit_times: list[float] = []
    frame_idx = 0

    try:
        from tqdm import tqdm
        pbar = tqdm(total=total_frames // frame_interval, unit="frame", desc="Scanning video")
    except ImportError:
        pbar = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            timestamp = frame_idx / fps

            try:
                results = DeepFace.represent(
                    img_path=frame,
                    model_name="ArcFace",
                    enforce_detection=False,   # don't raise if no face found
                    detector_backend="retinaface",
                )
                for face in results:
                    emb = np.array(face["embedding"])
                    emb = emb / np.linalg.norm(emb)
                    sim = _cosine_similarity(emb, reference_embedding)
                    if sim >= similarity_threshold:
                        hit_times.append(timestamp)
                        logger.debug(f"  Match at {timestamp:.1f}s (similarity={sim:.3f})")
                        break
            except Exception as e:
                logger.debug(f"Frame {frame_idx} ({timestamp:.1f}s): detection skipped — {e}")

            if pbar:
                pbar.update(1)

        frame_idx += 1

    cap.release()
    if pbar:
        pbar.close()

    logger.info(f"Found {len(hit_times)} positive frames. Building segments...")
    return _build_segments(hit_times, total_duration, gap_tolerance, min_segment_duration)


def _build_segments(
    hit_times: list[float],
    video_duration: float,
    gap_tolerance: float,
    min_duration: float,
) -> list[Segment]:
    if not hit_times:
        return []

    segments: list[Segment] = []
    seg_start = hit_times[0]
    seg_end = hit_times[0]

    for t in hit_times[1:]:
        if t - seg_end <= gap_tolerance:
            seg_end = t
        else:
            segments.append(Segment(start=seg_start, end=seg_end))
            seg_start = t
            seg_end = t

    segments.append(Segment(start=seg_start, end=seg_end))

    segments = [s for s in segments if s.duration >= min_duration]

    logger.info(f"Segments after merging and filtering: {len(segments)}")
    for i, s in enumerate(segments):
        logger.info(f"  [{i+1}] {s.start:.1f}s – {s.end:.1f}s ({s.duration:.1f}s)")

    return segments
