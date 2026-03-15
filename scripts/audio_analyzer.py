"""
audio_analyzer.py

Audio analysis pipeline for a single clip:
  1. Demucs  — source separation, isolate the "other+vocals" stem (horn)
  2. basic-pitch — pitch detection & MIDI transcription
  3. Note stats — computed from MIDI note events (no extra dependencies)
"""

import logging
import shutil
import subprocess
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _midi_to_name(midi: int) -> str:
    note = _NOTE_NAMES[midi % 12]
    octave = (midi // 12) - 1
    return f"{note}{octave}"


def separate_stems(audio_path: str, output_dir: str) -> dict[str, str]:
    """
    Run Demucs CLI and return paths to separated stems.
    The "other+vocals" mix isolates horn/brass instruments.
    """
    demucs = shutil.which("demucs") or "demucs"
    out = Path(output_dir) / "demucs"

    logger.info("Running Demucs source separation (this may take a while)...")
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

    # Mix other + vocals to capture brass bleed (trombone often lands in vocals stem)
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


def transcribe_pitch(audio_path: str, output_dir: str) -> dict:
    """
    Run basic-pitch via CLI subprocess and parse the resulting MIDI file.
    Using the CLI avoids TF/tf-keras/onnxruntime version incompatibilities
    that plague the Python API across package upgrades.
    Returns {"midi_path": str | None, "note_events": list[dict]}
    """
    import sys

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    logger.info("Running basic-pitch transcription...")
    try:
        basic_pitch_bin = str(Path(sys.executable).parent / "basic-pitch")
        result = subprocess.run(
            [basic_pitch_bin, "--model-serialization", "onnx", str(out), audio_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.warning(f"basic-pitch failed:\n{result.stderr[-500:]}")
            return {"midi_path": None, "note_events": []}

        # CLI outputs <stem>_basic_pitch.mid
        stem = Path(audio_path).stem
        candidates = list(out.glob(f"{stem}*.mid"))
        if not candidates:
            candidates = list(out.glob("*.mid"))
        if not candidates:
            logger.warning("basic-pitch produced no MIDI file")
            return {"midi_path": None, "note_events": []}

        midi_path = str(candidates[0])

        import pretty_midi
        pm = pretty_midi.PrettyMIDI(midi_path)
        events = []
        for instrument in pm.instruments:
            for note in instrument.notes:
                events.append({
                    "start_time": round(float(note.start), 4),
                    "end_time": round(float(note.end), 4),
                    "pitch_midi": int(note.pitch),
                    "amplitude": round(float(note.velocity) / 127.0, 4),
                })
        events.sort(key=lambda n: n["start_time"])

        logger.info(f"Transcribed {len(events)} note events. MIDI: {midi_path}")
        return {"midi_path": midi_path, "note_events": events}
    except Exception as e:
        logger.warning(f"Pitch transcription failed: {e}")
        return {"midi_path": None, "note_events": []}


def compute_note_stats(note_events: list[dict]) -> dict:
    """
    Derive musical stats from a list of note event dicts.
    All values are JSON-serializable.
    """
    if not note_events:
        return {
            "note_count": 0,
            "most_common_notes": [],
            "highest_note": None,
            "lowest_note": None,
            "pitch_range": None,
            "avg_note_duration_s": None,
            "note_density_per_s": None,
            "longest_phrase_notes": None,
            "longest_phrase_duration_s": None,
        }

    pitches = [n["pitch_midi"] for n in note_events]
    durations = [n["end_time"] - n["start_time"] for n in note_events]
    sorted_notes = sorted(note_events, key=lambda n: n["start_time"])

    # Highest / lowest with octave (e.g. "Bb4", "F2")
    highest_note = _midi_to_name(max(pitches))
    lowest_note = _midi_to_name(min(pitches))
    pitch_range = max(pitches) - min(pitches)

    # Most common pitch classes (top 5, ignoring octave)
    pitch_class_counts = Counter(_NOTE_NAMES[p % 12] for p in pitches)
    most_common_notes = [
        {"note": n, "count": c} for n, c in pitch_class_counts.most_common(5)
    ]

    # Average note duration
    avg_note_duration_s = round(sum(durations) / len(durations), 3)

    # Note density over the span of the solo
    span = sorted_notes[-1]["end_time"] - sorted_notes[0]["start_time"]
    note_density_per_s = round(len(note_events) / span, 2) if span > 0 else None

    # Longest phrase — a gap > 0.5s is treated as a rest between phrases
    REST_THRESHOLD_S = 0.5
    phrases: list[list[dict]] = []
    current: list[dict] = [sorted_notes[0]]
    for note in sorted_notes[1:]:
        if note["start_time"] - current[-1]["end_time"] > REST_THRESHOLD_S:
            phrases.append(current)
            current = [note]
        else:
            current.append(note)
    phrases.append(current)

    longest = max(phrases, key=len)
    longest_phrase_notes = len(longest)
    longest_phrase_duration_s = round(
        longest[-1]["end_time"] - longest[0]["start_time"], 2
    )

    return {
        "note_count": len(note_events),
        "most_common_notes": most_common_notes,
        "highest_note": highest_note,
        "lowest_note": lowest_note,
        "pitch_range": pitch_range,
        "avg_note_duration_s": avg_note_duration_s,
        "note_density_per_s": note_density_per_s,
        "longest_phrase_notes": longest_phrase_notes,
        "longest_phrase_duration_s": longest_phrase_duration_s,
    }


def analyze_clip(audio_path: str, output_dir: str, skip_demucs: bool = False) -> dict:
    """
    Full analysis pipeline for a single clip:
      1. Demucs stem separation (isolate horn)
      2. basic-pitch MIDI transcription
      3. Note stats derived from MIDI events
    """
    # Step 1: Source separation
    if skip_demucs:
        stem_audio = audio_path
    else:
        stems = separate_stems(audio_path, output_dir)
        stem_audio = stems.get("other", audio_path)

    # Step 2: Pitch transcription on isolated stem
    transcription_dir = str(Path(output_dir) / "midi")
    pitch_result = transcribe_pitch(stem_audio, transcription_dir)

    # Step 3: Stats derived from note events
    stats = compute_note_stats(pitch_result["note_events"])

    return {
        "midi_path": pitch_result["midi_path"],
        "note_events": pitch_result["note_events"],
        **stats,
    }
