"""Run the sequence-level stuck pass over a frozen fixture and check it
against the fixture's `stuck_episodes` ground truth.

No daemon, no Ollama — stuck detection is pure Python over the frame
metadata + text. Sub-second.

    python tests/eval_stuck.py [--fixture PATH] [--profile NAME]
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

from ocr_provider import merge_text  # noqa: E402
from profiles import PROFILES  # noqa: E402
from stuck import detect_stuck, format_findings  # noqa: E402

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
    expected = fixture.get("stuck_episodes", [])

    # the fixture stores relative offsets; rebuild absolute timestamps so
    # detect_stuck sees real dwell times
    base = datetime(2000, 1, 1)
    captures = [{
        "text": frame_text(f, args.text_source),
        "timestamp": (base + timedelta(seconds=f["t_offset_s"])).isoformat(),
        "browser_url": f["browser_url"],
        "capture_trigger": f.get("capture_trigger", ""),
    } for f in frames]

    findings = detect_stuck(captures, profile)

    print(f"fixture: {args.fixture.name}  ({len(frames)} frames)")
    print(f"profile: {args.profile}   text-source: {args.text_source}\n")

    print("findings:")
    print(format_findings(findings) or "  (none)")
    print()

    found_q = {f["question"] for f in findings}
    exp_q = {e["question"] for e in expected}

    missed = exp_q - found_q
    spurious = found_q - exp_q

    for e in expected:
        mark = "OK " if e["question"] in found_q else "XX "
        print(f"  {mark} expect stuck on {e['question']:6s}  ({e['note'][:60]})")
    for q in sorted(spurious):
        print(f"  XX unexpected stuck finding on {q}")

    ok = not missed and not spurious
    # flavour check (only when the episode was found and ground truth names one)
    for e in expected:
        if e["question"] in found_q and "flavour" in e:
            got = next(f["flavour"] for f in findings if f["question"] == e["question"])
            if got != e["flavour"]:
                ok = False
                print(f"  XX {e['question']}: flavour {got!r} != expected {e['flavour']!r}")

    print(f"\n  {'PASS' if ok else 'FAIL'}  "
          f"({len(exp_q & found_q)}/{len(exp_q)} expected, {len(spurious)} spurious)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
