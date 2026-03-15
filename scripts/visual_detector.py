"""
visual_detector.py

CLIP-based visual second pass for trombone detection.

After audio CLAP finds candidate segments, this module samples frames from each
segment and checks whether a trombone is visible using CLIP image-text similarity.
This exploits a completely different signal from the audio pass: the trombone slide
in playing position is visually distinctive (no trumpet or saxophone has a slide).

Usage as module:
    from visual_detector import TromboneVisualDetector
    detector = TromboneVisualDetector()
    detector.load(reference_images=["./me_playing.jpg"])
    segments = detector.filter_segments(video_path, segments, threshold=0.0)

Usage as CLI (for threshold calibration):
    python visual_detector.py \\
        --video ./output/video/smalls.MP4 \\
        --start 5823 --end 5889 \\
        [--reference-images ./me1.jpg ./me2.jpg] \\
        [--frames 8]
"""

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Text prompts used to query CLIP.
# Positive = trombone; Negative = other brass instruments we're likely to confuse with.
# These work without any reference images; reference images improve accuracy in dark venues.
_POSITIVE_PROMPTS = [
    "a person playing trombone on stage",
    "jazz trombone player on stage",
    "brass instrument with a slide being played",
    "trombone slide extended while playing",
]
_NEGATIVE_PROMPTS = [
    "a person playing trumpet",
    "jazz trumpet player on stage",
    "a person playing saxophone",
    "saxophonist performing on stage",
]

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


@dataclass
class VisualScore:
    start: float
    end: float
    score: float          # positive = trombone likely; negative = other instrument likely
    peak_frame: float     # timestamp of the highest-scoring frame
    n_frames: int


class TromboneVisualDetector:
    """
    Uses CLIP image-text (and optionally image-image) similarity to verify
    that a trombone is visible in the frames of an audio-matched segment.

    Score = mean(positive_similarities across all reference images) - mean(negative_similarities)
    averaged over sampled frames. Positive score → trombone more likely.

    If reference_images are provided, their CLIP embeddings are added to the
    positive pool alongside the text prompts. This is more robust than text
    alone for dark club lighting.
    """

    model_id = "openai/clip-vit-base-patch32"

    def load(self, reference_images: list[str] | None = None) -> None:
        from transformers import CLIPModel, CLIPProcessor
        import torch

        logger.info(f"Loading CLIP visual model: {self.model_id}")
        self._processor = CLIPProcessor.from_pretrained(self.model_id)
        self._model = CLIPModel.from_pretrained(self.model_id)
        self._model.eval()

        if torch.backends.mps.is_available():
            self._device = "mps"
        elif torch.cuda.is_available():
            self._device = "cuda"
        else:
            self._device = "cpu"
        self._model = self._model.to(self._device)

        # Build reference pool: reference images take priority over text prompts.
        # Image-to-image similarity is more specific than text — it matches
        # your face, your instrument position, your camera angle, not just
        # "any jazz stage scene."
        valid_refs = [p for p in (reference_images or []) if Path(p).exists()]
        if valid_refs:
            self._ref_embs = self._embed_image_files(valid_refs)  # (K, D)
            logger.info(f"Using {len(valid_refs)} reference image(s) for image-image scoring")
            missing = len(reference_images or []) - len(valid_refs)
            if missing:
                logger.warning(f"{missing} reference image(s) not found on disk")
        else:
            # Fall back to text prompts if no reference images provided
            logger.warning("No reference images provided — falling back to text prompts only")
            self._ref_embs = self._embed_texts(_POSITIVE_PROMPTS)

        self._neg_embs = self._embed_texts(_NEGATIVE_PROMPTS)

        logger.info(
            f"CLIP visual detector ready on {self._device} "
            f"({len(self._ref_embs)} reference, {len(self._neg_embs)} negative embeddings)"
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def score_segment(
        self,
        video_path: str,
        start: float,
        end: float,
        n_frames: int = 6,
        save_frames_dir: str | None = None,
    ) -> VisualScore:
        """
        Sample n_frames evenly from [start, end] and return a VisualScore.
        Score > 0 means trombone prompts scored higher than other-instrument prompts.

        If save_frames_dir is given, extracted frames are saved there as
        frame_<timestamp>_<score>.jpg so you can inspect what CLIP is seeing.
        """
        timestamps = np.linspace(start, end, n_frames).tolist()
        frame_scores: list[tuple[float, float]] = []  # (timestamp, score)

        save_dir = Path(save_frames_dir) if save_frames_dir else None
        if save_dir:
            save_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            for ts in timestamps:
                frame_path = Path(tmp) / f"frame_{ts:.1f}.jpg"
                ok = _extract_frame(video_path, ts, str(frame_path))
                if not ok:
                    continue
                img_emb = self._embed_image_file(str(frame_path))
                pos_sim = float(np.dot(img_emb, self._ref_embs.T).mean())  # avg across reference images
                neg_sim = float(np.dot(img_emb, self._neg_embs.T).mean())
                sc = pos_sim - neg_sim
                frame_scores.append((ts, sc))
                if save_dir:
                    dest = save_dir / f"frame_{ts:.1f}s_score{sc:+.3f}.jpg"
                    import shutil as _sh
                    _sh.copy2(str(frame_path), str(dest))

        if not frame_scores:
            return VisualScore(start=start, end=end, score=0.0, peak_frame=start, n_frames=0)

        best_ts, best_sc = max(frame_scores, key=lambda x: x[1])
        mean_score = float(np.mean([sc for _, sc in frame_scores]))

        logger.debug(
            f"  Visual [{start:.1f}s–{end:.1f}s]: mean={mean_score:.3f} "
            f"peak={best_sc:.3f}@{best_ts:.1f}s  "
            + "  ".join(f"{ts:.1f}:{sc:.3f}" for ts, sc in sorted(frame_scores))
        )

        return VisualScore(
            start=start,
            end=end,
            score=mean_score,
            peak_frame=best_ts,
            n_frames=len(frame_scores),
        )

    def filter_segments(
        self,
        video_path: str,
        segments: list,  # list[AudioSegment]
        threshold: float = 0.0,
        n_frames: int = 6,
    ) -> tuple[list, list[VisualScore]]:
        """
        Score each segment visually and return (kept_segments, all_visual_scores).
        Segments scoring below threshold are filtered out.
        """
        kept = []
        scores: list[VisualScore] = []

        for i, seg in enumerate(segments):
            vs = self.score_segment(video_path, seg.start, seg.end, n_frames=n_frames)
            scores.append(vs)
            marker = "✓" if vs.score >= threshold else "✗"
            audio_peak = getattr(seg, "peak_score", None)
            peak_str = f" audio_peak={audio_peak:.3f}" if audio_peak is not None else ""
            logger.info(
                f"  Visual {marker} segment {i+1} [{seg.start:.1f}s–{seg.end:.1f}s]:"
                f"{peak_str} visual={vs.score:.3f} (threshold={threshold})"
            )
            if vs.score >= threshold:
                kept.append(seg)

        logger.info(
            f"Visual filter: {len(segments)} → {len(kept)} segments "
            f"(threshold={threshold})"
        )
        return kept, scores

    # ── Embedding helpers ──────────────────────────────────────────────────────

    def _embed_texts(self, texts: list[str]) -> np.ndarray:
        import torch
        inputs = self._processor(text=texts, return_tensors="pt", padding=True)
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        with torch.no_grad():
            embs = self._model.get_text_features(**inputs)
        if not isinstance(embs, torch.Tensor):
            embs = embs.pooler_output
        embs = embs.cpu().numpy().astype(np.float32)
        return _l2_norm(embs)

    def _embed_image_file(self, path: str) -> np.ndarray:
        from PIL import Image
        import torch
        img = Image.open(path).convert("RGB")
        inputs = self._processor(images=img, return_tensors="pt")
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        with torch.no_grad():
            emb = self._model.get_image_features(**inputs)
        if not isinstance(emb, torch.Tensor):
            emb = emb.pooler_output
        emb = emb.cpu().numpy().astype(np.float32)
        return _l2_norm(emb)[0]

    def _embed_image_files(self, paths: list[str]) -> np.ndarray:
        return np.vstack([self._embed_image_file(p) for p in paths])


# ── Utilities ──────────────────────────────────────────────────────────────────

def _l2_norm(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.where(norms == 0, 1, norms)


def _extract_frame(video_path: str, timestamp: float, out_path: str) -> bool:
    """Extract a single frame at timestamp seconds. Returns True on success."""
    result = subprocess.run(
        [
            FFMPEG, "-y",
            "-ss", str(timestamp),
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            out_path,
        ],
        capture_output=True,
    )
    return result.returncode == 0 and Path(out_path).exists()


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Score a video segment for trombone visibility using CLIP"
    )
    parser.add_argument("--video", required=True, help="Video file to sample frames from")
    parser.add_argument("--start", type=float, required=True, help="Segment start (seconds)")
    parser.add_argument("--end", type=float, required=True, help="Segment end (seconds)")
    parser.add_argument("--reference-images", nargs="*", default=[],
                        help="Reference images of you playing trombone (used as additional positive embeddings)")
    parser.add_argument("--frames", type=int, default=8,
                        help="Number of frames to sample from the segment (default: 8)")
    parser.add_argument("--threshold", type=float, default=0.0,
                        help="Score threshold — above this = trombone detected (default: 0.0)")
    parser.add_argument("--save-frames", default=None, metavar="DIR",
                        help="Save extracted frames to this directory as "
                             "frame_<timestamp>_<score>.jpg for visual inspection")
    args = parser.parse_args()

    detector = TromboneVisualDetector()
    detector.load(reference_images=args.reference_images)

    logging.getLogger().setLevel(logging.DEBUG)  # show per-frame scores in CLI mode
    vs = detector.score_segment(
        args.video, args.start, args.end,
        n_frames=args.frames,
        save_frames_dir=args.save_frames,
    )

    print(f"\nSegment [{args.start:.1f}s – {args.end:.1f}s]")
    print(f"  Visual score : {vs.score:.4f}  ({'TROMBONE' if vs.score >= args.threshold else 'other/uncertain'})")
    print(f"  Peak frame   : {vs.peak_frame:.1f}s")
    print(f"  Frames scored: {vs.n_frames}/{args.frames}")
    print(f"  Threshold    : {args.threshold}")
