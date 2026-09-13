"""Print the LLM narrative for each stuck-detection finding in a fixture.

No pass/fail — narrative quality needs a human read, not exact-match
grading. This is the acknowledged tradeoff of adding an LLM pass on top
of the deterministic rules. Decoding is deterministic
(temperature=0, same as classify()), so a rerun that produces a different
narrative for the same finding means something upstream moved (the text
pipeline, the fixture, the prompt) — read this as a stability check as
much as a quality one.

Requires Ollama running with the model from classify_recent_activity.

    python tests/eval_narrate.py [--fixture PATH] [--profile NAME]
                                 [--text-source screenpipe|ocr|merged]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from classify_recent_activity import add_episode_signals  # noqa: E402
from narrate import add_narratives  # noqa: E402
from ocr_provider import merge_text  # noqa: E402
from profiles import PROFILES  # noqa: E402
from stuck import detect_stuck, format_findings  # noqa: E402

DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "gretel_calibration.json"


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

    base = datetime(2000, 1, 1)
    captures = [{
        "text": frame_text(f, args.text_source),
        "timestamp": (base + timedelta(seconds=f["t_offset_s"])).isoformat(),
        "browser_url": f["browser_url"],
        "capture_trigger": f.get("capture_trigger", ""),
    } for f in frames]

    findings = detect_stuck(captures, profile)
    print(f"fixture: {args.fixture.name}  ({len(frames)} frames, {len(findings)} finding(s))\n")
    if not findings:
        print("(no stuck episodes found — nothing to narrate)")
        return

    add_episode_signals(findings, captures)
    add_narratives(findings, captures, profile)
    print(format_findings(findings))


if __name__ == "__main__":
    main()
