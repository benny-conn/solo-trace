"""
audio_embedder.py

Finds segments in a long audio/video recording where a specific player
is performing, using neural audio embeddings for similarity search.

A reference clip of the player is embedded once; a sliding window over
the target audio is compared against it via cosine similarity.

Supported models (swap with --model flag):
  clap  — laion/clap-htsat-unfused  (general audio, fast, good baseline)
  mert  — m-a-p/MERT-v1-95M         (music-specific, potentially more accurate)

Both use the HuggingFace transformers interface; swapping is a one-liner.

Usage as module:
    from audio_embedder import detect_similar_segments, CLAPEmbedder
    embedder = CLAPEmbedder()
    embedder.load()
    segments = detect_similar_segments("me.wav", "full_audio.wav", embedder)

Usage as CLI (for threshold tuning without re-running the full pipeline):
    python audio_embedder.py \\
        --reference ./me.mov \\
        --audio ./output/full_audio.wav \\
        --model clap \\
        --threshold 0.85
"""

import logging
import shutil
import subprocess
import tempfile  # still used by _ensure_wav
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AudioSegment:
    start: float
    end: float
    peak_score: float = 0.0
    hit_count: int = 0
    total_windows: int = 0

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def hit_ratio(self) -> float:
        return self.hit_count / self.total_windows if self.total_windows > 0 else 0.0


# ── Embedder base class ────────────────────────────────────────────────────────

class Embedder:
    """
    Base class for audio embedders.

    Subclasses must set `sample_rate` and implement `embed_batch()`.
    `load()` should initialise the model (called once before use).
    """
    model_id: str = ""
    sample_rate: int = 22050

    def load(self) -> None:
        raise NotImplementedError

    def embed_batch(self, windows: list[np.ndarray]) -> np.ndarray:
        """
        Embed a list of mono float32 audio arrays (each at self.sample_rate).
        Returns an (N, D) array of L2-normalised embedding vectors.
        """
        raise NotImplementedError

    # Convenience: embed a single array
    def embed(self, y: np.ndarray) -> np.ndarray:
        return self.embed_batch([y])[0]

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        return float(np.dot(a, b) / denom) if denom > 0 else 0.0

    @staticmethod
    def _get_device():
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"


# ── CLAP ──────────────────────────────────────────────────────────────────────

class CLAPEmbedder(Embedder):
    """
    LAION CLAP — Contrastive Language-Audio Pretraining.
    General audio understanding; audio-to-audio similarity works well.
    Model: laion/clap-htsat-unfused (~900 MB, downloaded on first use).
    """
    model_id = "laion/clap-htsat-unfused"
    sample_rate = 48000   # CLAP requires 48 kHz

    def load(self) -> None:
        from transformers import ClapModel, ClapProcessor
        import torch

        logger.info(f"Loading CLAP model: {self.model_id}")
        self._processor = ClapProcessor.from_pretrained(self.model_id)
        self._model = ClapModel.from_pretrained(self.model_id)
        self._model.eval()
        self._device = self._get_device()
        self._model = self._model.to(self._device)
        logger.info(f"CLAP loaded on {self._device}")

    def embed_batch(self, windows: list[np.ndarray]) -> np.ndarray:
        import torch

        inputs = self._processor(
            audio=windows,
            return_tensors="pt",
            sampling_rate=self.sample_rate,
            padding=True,
        )
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        with torch.no_grad():
            features = self._model.get_audio_features(**inputs)
        # Older transformers returns a tensor; newer returns a model output object
        if not isinstance(features, torch.Tensor):
            features = features.pooler_output
        embs = features.cpu().numpy().astype(np.float32)
        # L2 normalise
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        return embs / np.where(norms == 0, 1, norms)


# ── MERT ──────────────────────────────────────────────────────────────────────

class MERTEmbedder(Embedder):
    """
    MERT — Music Understanding Model (m-a-p/MERT-v1-95M).
    Pretrained specifically on music; tends to cluster instrument timbres well.
    Model: ~380 MB, downloaded on first use.
    """
    model_id = "m-a-p/MERT-v1-95M"
    sample_rate = 24000   # MERT requires 24 kHz

    def load(self) -> None:
        from transformers import AutoModel, Wav2Vec2FeatureExtractor
        import torch

        logger.info(f"Loading MERT model: {self.model_id}")
        self._processor = Wav2Vec2FeatureExtractor.from_pretrained(
            self.model_id, trust_remote_code=True
        )
        self._model = AutoModel.from_pretrained(
            self.model_id, trust_remote_code=True
        )
        self._model.eval()
        self._device = self._get_device()
        self._model = self._model.to(self._device)
        logger.info(f"MERT loaded on {self._device}")

    def embed_batch(self, windows: list[np.ndarray]) -> np.ndarray:
        import torch

        inputs = self._processor(
            windows,
            sampling_rate=self.sample_rate,
            return_tensors="pt",
            padding=True,
        )
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self._model(**inputs, output_hidden_states=True)
        # Mean-pool the last hidden state → one vector per window
        embs = outputs.last_hidden_state.mean(dim=1).cpu().numpy().astype(np.float32)
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        return embs / np.where(norms == 0, 1, norms)


# ── Detection ─────────────────────────────────────────────────────────────────

def _has_low_brass_notes(
    y: np.ndarray,
    sr: int,
    max_hz: float = 180.0,
    min_fraction: float = 0.05,
) -> bool:
    """
    Return True if this audio window contains notes in trombone-exclusive range.

    Trumpet's lowest note is F#3 (~185 Hz). Trombone reaches down to Bb1 (~58 Hz).
    Any voiced frame with fundamental below max_hz is physically impossible on trumpet,
    so even a small fraction of such frames confirms it's trombone.

    Uses librosa pyin (probabilistic YIN) for robust pitch estimation.
    Falls back to True (don't filter) on any error.
    """
    try:
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y,
            fmin=librosa.note_to_hz("C1"),   # ~32 Hz — below any instrument
            fmax=librosa.note_to_hz("C6"),   # ~1047 Hz — above trumpet range
            sr=sr,
        )
        # Confident voiced frames only
        confident = voiced_flag & (voiced_probs > 0.5)
        voiced_f0 = f0[confident]
        if len(voiced_f0) == 0:
            return False
        low_fraction = float(np.mean(voiced_f0 < max_hz))
        return low_fraction >= min_fraction
    except Exception:
        return True   # Don't filter on error


def detect_similar_segments(
    reference_path: str,
    target_path: str,
    embedder: Embedder,
    similarity_threshold: float = 0.85,
    window_seconds: float = 8.0,
    hop_seconds: float = 2.0,
    batch_size: int = 32,
    min_segment_duration: float = 40.0,
    gap_tolerance: float = 30.0,
    min_peak_score: float = 0.0,
    pitch_filter: bool = False,
    pitch_max_hz: float = 180.0,
    pitch_min_fraction: float = 0.05,
    isolate_reference_stem: bool = False,
) -> list[AudioSegment]:
    """
    Find segments in target_path that sound similar to reference_path.

    Args:
        reference_path: Audio/video file of the reference player (e.g. me.mov).
        target_path:    Full audio WAV of the recording to search.
        embedder:       Loaded Embedder instance (CLAPEmbedder, MERTEmbedder, …).
        similarity_threshold: Cosine similarity cutoff [0–1]. Start around 0.85
                        for CLAP, 0.80 for MERT; tune with the CLI.
        window_seconds: Sliding window size.
        hop_seconds:    Step between windows.
        batch_size:     Windows to embed at once (larger = faster on GPU/MPS).
        min_segment_duration: Drop segments shorter than this.
        gap_tolerance:  Merge segments whose gap is smaller than this.
        pitch_filter:   If True, discard CLAP-matched windows where no notes
                        below pitch_max_hz are detected. Caveat: bass is always
                        present in the low range, so this is a weak signal on
                        mixed audio. More useful on isolated stems.
        pitch_max_hz:   Fundamental frequency ceiling for trombone-exclusive range.
                        Trumpet lowest note is ~185 Hz; default 180 is conservative.
        pitch_min_fraction: Fraction of voiced frames that must be below pitch_max_hz.
        isolate_reference_stem: If True, run Demucs on the reference audio before
                        embedding it. Produces a cleaner trombone-only reference
                        which may improve CLAP separation from other brass.
                        Only practical on short reference clips (me.mov).

    Returns:
        List of AudioSegment sorted by start time.
    """
    # Build reference embedding (mean of multiple windows for robustness)
    ref_embedding = _embed_reference(reference_path, embedder, isolate_stem=isolate_reference_stem)
    logger.info("Reference embedding computed.")

    # Load target audio at the embedder's required sample rate
    logger.info(f"Loading target audio: {target_path}")
    y, sr = librosa.load(target_path, sr=embedder.sample_rate, mono=True)
    total_duration = len(y) / sr

    window_samples = int(window_seconds * sr)
    hop_samples = int(hop_seconds * sr)

    # Build all windows
    starts = list(range(0, len(y) - window_samples, hop_samples))
    logger.info(
        f"Scanning {total_duration:.0f}s — {len(starts)} windows "
        f"(window={window_seconds}s, hop={hop_seconds}s, threshold={similarity_threshold})"
    )

    hit_times: list[float] = []
    all_scores: dict[float, float] = {}  # timestamp → similarity (all windows)

    try:
        from tqdm import tqdm
        pbar = tqdm(total=len(starts), unit="win", desc=f"Embedding [{embedder.model_id.split('/')[-1]}]")
    except ImportError:
        pbar = None

    for batch_start in range(0, len(starts), batch_size):
        batch_starts = starts[batch_start:batch_start + batch_size]
        windows = [y[s:s + window_samples] for s in batch_starts]
        timestamps = [s / sr for s in batch_starts]

        try:
            embs = embedder.embed_batch(windows)
            for ts, emb in zip(timestamps, embs):
                sim = float(np.dot(emb, ref_embedding))   # both L2-normed
                all_scores[ts] = sim
                if sim >= similarity_threshold:
                    hit_times.append(ts)
        except Exception as e:
            logger.warning(f"Batch at {batch_starts[0]/sr:.1f}s failed: {e}")

        if pbar:
            pbar.update(len(batch_starts))

    if pbar:
        pbar.close()

    # Log top scores for threshold tuning
    sorted_scores = sorted(all_scores.items(), key=lambda x: x[1], reverse=True)
    logger.info("Top 10 similarity scores:")
    for ts, sim in sorted_scores[:10]:
        bar = "█" * int(sim * 30)
        logger.info(f"  {ts:8.1f}s  {sim:.3f}  {bar}")
    logger.info(f"  threshold={similarity_threshold} — {len(hit_times)} windows matched")

    # Optional pitch filter — runs pyin on matched windows only
    if pitch_filter and hit_times:
        logger.info(
            f"Applying pitch filter (max_hz={pitch_max_hz}, min_fraction={pitch_min_fraction}). "
            f"Note: bass contaminates low range in full-band mixes; use with isolated stems for best results."
        )
        # Load raw audio for pitch detection (22050 Hz is enough)
        y_pitch, sr_pitch = librosa.load(target_path, sr=22050, mono=True)
        win_p = int(window_seconds * sr_pitch)
        before = len(hit_times)
        hit_times = [
            t for t in hit_times
            if _has_low_brass_notes(
                y_pitch[int(t * sr_pitch): int(t * sr_pitch) + win_p],
                sr_pitch,
                max_hz=pitch_max_hz,
                min_fraction=pitch_min_fraction,
            )
        ]
        logger.info(f"Pitch filter: {before} → {len(hit_times)} windows kept")

    segments = _build_segments(hit_times, all_scores, total_duration, gap_tolerance, min_segment_duration, window_seconds)
    if min_peak_score > 0:
        before = len(segments)
        segments = [s for s in segments if s.peak_score >= min_peak_score]
        if len(segments) < before:
            logger.info(f"Peak filter (>={min_peak_score}): {before} → {len(segments)} segments")
    _log_segment_scores(segments, all_scores, similarity_threshold, window_seconds)
    return segments


def _embed_reference(reference_path: str, embedder: Embedder, isolate_stem: bool = False) -> np.ndarray:
    """
    Embed a reference recording. Extracts multiple 8s windows, embeds all,
    returns the mean embedding (more robust than embedding the whole file at once).

    If isolate_stem=True, runs Demucs on the reference first to get the horn
    stem (other+vocals mix), producing a cleaner trombone-focused embedding.
    Only feasible on short clips (~minutes).
    """
    audio_path = _ensure_wav(reference_path, 22050 if isolate_stem else embedder.sample_rate)

    if isolate_stem:
        import subprocess as sp
        logger.info("Running Demucs on reference audio for cleaner stem embedding...")
        # Save stems next to the reference file so you can inspect them
        ref_p = Path(reference_path).resolve()
        stems_dir = ref_p.parent / f"{ref_p.stem}_stems"
        stems_dir.mkdir(exist_ok=True)
        try:
            sp.run([
                "python", "-m", "demucs",
                "--name", "htdemucs_6s",
                "--out", str(stems_dir),
                audio_path,
            ], check=True)
            track = Path(audio_path).stem
            other = stems_dir / "htdemucs_6s" / track / "other.wav"
            vocals = stems_dir / "htdemucs_6s" / track / "vocals.wav"
            horn_mixed = stems_dir / "horn_mixed.wav"
            ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
            sp.run([
                ffmpeg, "-y",
                "-i", str(other), "-i", str(vocals),
                "-filter_complex", "amix=inputs=2:normalize=0",
                str(horn_mixed),
            ], check=True, capture_output=True)
            logger.info(f"Stems saved to: {stems_dir}")
            logger.info(f"  other.wav  — non-piano/bass/drums/guitar stem")
            logger.info(f"  vocals.wav — vocals stem (trombone bleeds here)")
            logger.info(f"  horn_mixed.wav — other+vocals mix used as reference")
            audio_path = _ensure_wav(str(horn_mixed), embedder.sample_rate)
            logger.info("Using demucs horn-mixed stem as reference.")
        except Exception as e:
            logger.warning(f"Demucs on reference failed, using raw audio: {e}")
            audio_path = _ensure_wav(reference_path, embedder.sample_rate)
    else:
        audio_path = _ensure_wav(reference_path, embedder.sample_rate)

    y, sr = librosa.load(audio_path, sr=embedder.sample_rate, mono=True)

    window_samples = int(8.0 * sr)
    hop_samples = int(4.0 * sr)
    windows = [
        y[s:s + window_samples]
        for s in range(0, len(y) - window_samples, hop_samples)
    ]
    if not windows:
        windows = [y[:window_samples] if len(y) >= window_samples else y]

    logger.info(f"Embedding reference from {len(windows)} windows: {reference_path}")
    embs = embedder.embed_batch(windows)
    mean_emb = embs.mean(axis=0)
    norm = np.linalg.norm(mean_emb)
    return mean_emb / norm if norm > 0 else mean_emb


def _ensure_wav(path: str, target_sr: int) -> str:
    """If path is a video or non-WAV file, extract audio to a temp WAV."""
    p = Path(path)
    if p.suffix.lower() == ".wav":
        return path
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    subprocess.run([
        ffmpeg, "-y", "-i", path,
        "-ac", "1", "-ar", str(target_sr), "-vn",
        tmp.name,
    ], check=True, capture_output=True)
    return tmp.name


def _log_segment_scores(
    segments: list[AudioSegment],
    all_scores: dict[float, float],
    threshold: float,
    window_seconds: float,
) -> None:
    """Log the per-window similarity scores that contributed to each segment."""
    for i, seg in enumerate(segments):
        # Collect all scored windows that fall within this segment's time range
        seg_scores = [
            (ts, sc) for ts, sc in all_scores.items()
            if seg.start <= ts <= seg.end
        ]
        seg_scores.sort(key=lambda x: x[0])
        peak = max(sc for _, sc in seg_scores) if seg_scores else 0.0
        n_hits = sum(1 for _, sc in seg_scores if sc >= threshold)
        logger.info(
            f"Segment {i+1} [{seg.start:.1f}s–{seg.end:.1f}s, {seg.duration:.0f}s] "
            f"peak={peak:.3f} hits={n_hits}/{len(seg_scores)} windows:"
        )
        for ts, sc in seg_scores:
            marker = "✓" if sc >= threshold else "·"
            bar = "█" * int(sc * 25)
            logger.info(f"    {marker} {ts:8.1f}s  {sc:.3f}  {bar}")


def _build_segments(
    hit_times: list[float],
    all_scores: dict[float, float],
    total_duration: float,
    gap_tolerance: float,
    min_duration: float,
    window_seconds: float = 8.0,
) -> list[AudioSegment]:
    if not hit_times:
        return []

    segments: list[AudioSegment] = []
    seg_start = hit_times[0]
    seg_end = hit_times[0]

    def _make_segment(start: float, end: float) -> AudioSegment:
        end = min(end + window_seconds, total_duration)
        window_scores = [sc for t, sc in all_scores.items() if start <= t <= end]
        hits = sum(1 for sc in window_scores if sc >= 0)  # all windows in range
        hit_wins = sum(1 for t in hit_times if start <= t <= end)
        peak = max(window_scores, default=0.0)
        return AudioSegment(start=start, end=end, peak_score=peak,
                            hit_count=hit_wins, total_windows=len(window_scores))

    for t in hit_times[1:]:
        if t - seg_end <= gap_tolerance:
            seg_end = t
        else:
            segments.append(_make_segment(seg_start, seg_end))
            seg_start = t
            seg_end = t

    segments.append(_make_segment(seg_start, seg_end))
    segments = [s for s in segments if s.duration >= min_duration]

    logger.info(f"Segments after merge+filter: {len(segments)}")
    for i, s in enumerate(segments):
        logger.info(f"  [{i+1}] {s.start:.1f}s – {s.end:.1f}s ({s.duration:.1f}s)")

    return segments


EMBEDDERS = {"clap": CLAPEmbedder, "mert": MERTEmbedder}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Find similar audio segments using neural embeddings")
    parser.add_argument("--reference", required=True, help="Reference audio/video file (e.g. me.mov)")
    parser.add_argument("--audio", required=True, help="Target audio WAV to search")
    parser.add_argument("--model", default="clap", choices=["clap", "mert"])
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--window", type=float, default=8.0)
    parser.add_argument("--hop", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--min-duration", type=float, default=40.0)
    parser.add_argument("--gap-tolerance", type=float, default=30.0)
    parser.add_argument("--min-peak", type=float, default=0.0,
                        help="Discard segments whose peak window score is below this. "
                             "E.g. 0.808 filters low-confidence brass without losing real solos.")
    parser.add_argument("--isolate-reference-stem", action="store_true",
                        help="Run Demucs on reference audio before embedding (cleaner trombone signal). "
                             "Only feasible on short reference clips.")
    parser.add_argument("--pitch-filter", action="store_true",
                        help="Discard windows with no notes below --pitch-max-hz. "
                             "Weak signal on full-band mixes; works better with isolated stems.")
    parser.add_argument("--pitch-max-hz", type=float, default=180.0)
    parser.add_argument("--pitch-min-fraction", type=float, default=0.05)
    args = parser.parse_args()

    embedder = EMBEDDERS[args.model]()
    embedder.load()

    segs = detect_similar_segments(
        reference_path=args.reference,
        target_path=args.audio,
        embedder=embedder,
        similarity_threshold=args.threshold,
        window_seconds=args.window,
        hop_seconds=args.hop,
        batch_size=args.batch_size,
        min_segment_duration=args.min_duration,
        gap_tolerance=args.gap_tolerance,
        min_peak_score=args.min_peak,
        pitch_filter=args.pitch_filter,
        pitch_max_hz=args.pitch_max_hz,
        pitch_min_fraction=args.pitch_min_fraction,
        isolate_reference_stem=args.isolate_reference_stem,
    )

    print(f"\n{len(segs)} segment(s) found:")
    for s in segs:
        print(f"  {s.start:.1f}s – {s.end:.1f}s ({s.duration:.1f}s)")
