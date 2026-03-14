"""
review_clips.py

Interactive CLI for reviewing clip detection quality after a pipeline run.
Opens each clip in QuickTime, shows detection metadata, and records your verdict.
Results are saved to review_results.json alongside the result.json.

Usage:
  python review_clips.py --result ./output/result.json
  python review_clips.py --result ./output/result.json --type audio_only  # review only one type

Controls:
  y / enter  — correct detection (keep)
  n          — false positive (wrong person / not playing)
  p          — partial (clipped start/end, threshold needs adjusting)
  s          — skip (undecided)
  q          — quit and save progress

At the end, prints a summary broken down by detection_type showing accuracy
per signal so you can see which thresholds to tune.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


LABELS = {
    "y": "correct",
    "":  "correct",   # enter = correct
    "n": "false_positive",
    "p": "partial",
    "s": "skip",
}

LABEL_DISPLAY = {
    "correct":       "✓ correct",
    "false_positive": "✗ false positive",
    "partial":       "~ partial",
    "skip":          "? skip",
}


def open_video(path: str) -> None:
    """Open video in default player (QuickTime on macOS)."""
    if not Path(path).exists():
        print(f"  [file not found: {path}]")
        return
    subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def review(result_path: str, filter_type: str | None = None) -> None:
    result_file = Path(result_path)
    if not result_file.exists():
        print(f"result.json not found: {result_path}")
        sys.exit(1)

    with open(result_file) as f:
        result = json.load(f)

    clips = result.get("clips", [])
    if not clips:
        print("No clips found in result.json")
        sys.exit(0)

    if filter_type:
        clips = [c for c in clips if c.get("detection_type") == filter_type]
        print(f"Filtered to {len(clips)} clip(s) with detection_type={filter_type!r}\n")

    # Load existing reviews if resuming
    review_file = result_file.parent / "review_results.json"
    reviews: dict[str, dict] = {}
    if review_file.exists():
        with open(review_file) as f:
            existing = json.load(f)
            reviews = {r["clip_index"]: r for r in existing.get("reviews", [])}
        print(f"Resuming — {len(reviews)} clip(s) already reviewed.\n")

    print(f"Video: {result.get('video_title', 'unknown')}")
    print(f"Total clips: {len(clips)}")
    print(f"Controls: [y/enter]=correct  [n]=false positive  [p]=partial  [s]=skip  [q]=quit\n")
    print("-" * 60)

    for clip in clips:
        idx = str(clip["clip_index"])

        if idx in reviews:
            existing_label = reviews[idx].get("label", "?")
            print(f"Clip {idx:>3} [{clip.get('detection_type','?'):>10}] — already reviewed: {existing_label}")
            continue

        dt = clip.get("detection_type", "unknown")
        start = fmt_time(clip.get("start", 0))
        end = fmt_time(clip.get("end", 0))
        duration = clip.get("duration", 0)
        bpm = clip.get("analysis", {}).get("bpm")
        key = clip.get("analysis", {}).get("key")
        mode = clip.get("analysis", {}).get("mode")

        print(f"\nClip {idx:>3} [{dt:>10}]  {start} – {end}  ({duration:.0f}s)", end="")
        if bpm:
            print(f"  {bpm:.0f}bpm", end="")
        if key:
            print(f"  {key} {mode or ''}", end="")
        print()

        video_path = clip.get("video_path", "")
        if video_path:
            open_video(video_path)

        while True:
            raw = input("  Label: ").strip().lower()
            if raw == "q":
                _save_reviews(reviews, result, review_file)
                _print_summary(reviews)
                print("\nProgress saved. Run again to continue.")
                sys.exit(0)
            if raw in LABELS:
                label = LABELS[raw]
                reviews[idx] = {
                    "clip_index": idx,
                    "detection_type": dt,
                    "label": label,
                    "video_path": video_path,
                    "start": clip.get("start"),
                    "end": clip.get("end"),
                }
                print(f"  → {LABEL_DISPLAY[label]}")
                break
            print("  Invalid input. Use y/n/p/s/q")

    _save_reviews(reviews, result, review_file)
    print("\n" + "=" * 60)
    _print_summary(reviews)
    print(f"\nResults saved to: {review_file}")


def _save_reviews(reviews: dict, result: dict, review_file: Path) -> None:
    out = {
        "video_title": result.get("video_title"),
        "video_url": result.get("video_url"),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviews": list(reviews.values()),
    }
    with open(review_file, "w") as f:
        json.dump(out, f, indent=2)


def _print_summary(reviews: dict) -> None:
    if not reviews:
        return

    # Group by detection_type
    by_type: dict[str, list[str]] = {}
    for r in reviews.values():
        dt = r.get("detection_type", "unknown")
        by_type.setdefault(dt, []).append(r.get("label", "skip"))

    print("Summary by detection type:")
    print(f"  {'Type':<12}  {'Total':>5}  {'Correct':>7}  {'False+':>7}  {'Partial':>7}  {'Skip':>5}  Accuracy")
    print("  " + "-" * 62)

    all_labels = []
    for dt in ["both", "face_only", "audio_only", "unknown"]:
        labels = by_type.get(dt)
        if not labels:
            continue
        all_labels.extend(labels)
        total = len(labels)
        correct = labels.count("correct")
        fp = labels.count("false_positive")
        partial = labels.count("partial")
        skip = labels.count("skip")
        reviewed = total - skip
        acc = f"{correct/reviewed:.0%}" if reviewed else "n/a"
        print(f"  {dt:<12}  {total:>5}  {correct:>7}  {fp:>7}  {partial:>7}  {skip:>5}  {acc}")

    total = len(all_labels)
    correct = all_labels.count("correct")
    fp = all_labels.count("false_positive")
    skip = all_labels.count("skip")
    reviewed = total - skip
    acc = f"{correct/reviewed:.0%}" if reviewed else "n/a"
    print("  " + "-" * 62)
    print(f"  {'TOTAL':<12}  {total:>5}  {correct:>7}  {fp:>7}  {'':>7}  {skip:>5}  {acc}")

    print()
    print("Threshold tuning hints:")
    for dt, labels in by_type.items():
        reviewed = [l for l in labels if l != "skip"]
        if not reviewed:
            continue
        fp_rate = labels.count("false_positive") / len(reviewed)
        partial_rate = labels.count("partial") / len(reviewed)
        if fp_rate > 0.3:
            print(f"  [{dt}] High false positive rate ({fp_rate:.0%}) → raise similarity threshold")
        if partial_rate > 0.3:
            print(f"  [{dt}] Many partial clips ({partial_rate:.0%}) → increase padding or gap_tolerance")
        if fp_rate < 0.05 and len(reviewed) >= 3:
            print(f"  [{dt}] Very clean ({fp_rate:.0%} FP) → could lower threshold to catch more clips")


def main() -> None:
    parser = argparse.ArgumentParser(description="Review clip detection quality")
    parser.add_argument("--result", required=True, help="Path to result.json from process_video.py")
    parser.add_argument("--type", dest="filter_type", default=None,
                        choices=["face_only", "audio_only", "both"],
                        help="Only review clips of this detection type")
    args = parser.parse_args()
    review(args.result, args.filter_type)


if __name__ == "__main__":
    main()
