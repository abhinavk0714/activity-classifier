"""Run the classifier over a frozen fixture and report per-segment accuracy.

Fast iteration loop for prompt / profile / OCR changes: no screenpipe
daemon, no re-capture, a few seconds per run (just the Ollama calls).

    python tests/eval_fixture.py [--fixture PATH] [--profile NAME]
                                 [--text-source screenpipe|ocr|merged]

text-source:
    screenpipe  screenpipe's own OCR text (English-only on bilingual UIs)
    ocr         a fresh ocr_provider pass (English + Japanese)
    merged      screenpipe's text + the non-Latin lines from ocr_provider
                — the approach intended for the engine (keeps screenpipe's
                stronger English, adds the Japanese it drops)

Requires Ollama running with the model from classify_recent_activity.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from classify_recent_activity import browser_urls, classify, dedup_text  # noqa: E402
from ocr_provider import merge_text  # noqa: E402
from profiles import PROFILES  # noqa: E402

DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "korero_bilingual.json"


def frame_text(frame: dict, source: str) -> str:
    if source == "screenpipe":
        return frame["screenpipe_text"]
    if source == "ocr":
        return frame["ocr_text"]
    if source == "merged":
        return merge_text(frame["screenpipe_text"], frame["ocr_text"])
    raise ValueError(source)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ap.add_argument("--profile", default="language_acquisition", choices=list(PROFILES))
    ap.add_argument("--text-source", default="merged",
                    choices=["screenpipe", "ocr", "merged"])
    args = ap.parse_args()

    fixture = json.loads(args.fixture.read_text())
    profile = PROFILES[args.profile]
    frames = fixture["frames"]

    # segments in first-seen order
    order: list[str] = []
    for f in frames:
        key = (f["segment"], f.get("segment_note", ""))
        if key not in order:
            order.append(key)

    print(f"fixture: {args.fixture.name}  ({len(frames)} frames)")
    print(f"profile: {args.profile}   text-source: {args.text_source}\n")

    correct = 0
    total = 0
    for label, note in order:
        seg_frames = [f for f in frames
                      if f["segment"] == label and f.get("segment_note", "") == note]
        caps = [{"text": frame_text(f, args.text_source),
                 "browser_url": f["browser_url"]} for f in seg_frames]
        text = dedup_text(caps)
        urls = browser_urls(caps)
        pred = classify(text, profile["labels"], profile["context"], "", urls)
        ok = pred.strip() == label
        correct += ok
        total += 1
        mark = "OK " if ok else "XX "
        note_s = f"  ({note[:48]})" if note else ""
        print(f"  {mark} {label:22s} -> {pred:22s}{note_s}")

    print(f"\n  {correct}/{total} correct")


if __name__ == "__main__":
    main()
