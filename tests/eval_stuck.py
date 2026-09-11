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
    not_expected = fixture.get("not_stuck_episodes", [])

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
    for f in findings:
        f["_t_from"] = (datetime.fromisoformat(f["t_start"]) - base).total_seconds()
        f["_t_to"] = (datetime.fromisoformat(f["t_end"]) - base).total_seconds()

    print(f"fixture: {args.fixture.name}  ({len(frames)} frames)")
    print(f"profile: {args.profile}   text-source: {args.text_source}\n")

    print("findings:")
    print(format_findings(findings) or "  (none)")
    print()

    def matches(e: dict, f: dict) -> bool:
        if e["question"] != f["question"]:
            return False
        # ground truth without a time window: question-only match (older
        # fixtures, or a question number that only ever appears once)
        if "t_from" not in e:
            return True
        return f["_t_from"] <= e["t_to"] and f["_t_to"] >= e["t_from"]

    ok = True
    matched_findings = set()
    hits = 0
    for e in expected:
        hit = next((f for f in findings if matches(e, f)), None)
        mark = "OK " if hit else "XX "
        if not hit:
            ok = False
        else:
            hits += 1
            matched_findings.add(id(hit))
            if "flavour" in e and hit["flavour"] != e["flavour"]:
                ok = False
                mark = "XX "
            if "resolved" in e and hit["resolved"] != e["resolved"]:
                ok = False
                mark = "XX "
        print(f"  {mark} expect     stuck on {e['question']:6s}  ({e['note'][:70]})")

    false_positives = 0
    for e in not_expected:
        hit = next((f for f in findings if matches(e, f)), None)
        mark = "XX " if hit else "OK "
        if hit:
            ok = False
            false_positives += 1
            matched_findings.add(id(hit))
        print(f"  {mark} expect NOT stuck on {e['question']:6s}  ({e['note'][:70]})")

    unexplained = [f for f in findings if id(f) not in matched_findings]
    for f in unexplained:
        ok = False
        print(f"  XX unexplained stuck finding on {f['question']} "
              f"(~{f['dwell_s']}s, t={f['_t_from']:.0f}-{f['_t_to']:.0f}s)")

    print(f"\n  {'PASS' if ok else 'FAIL'}  "
          f"({hits}/{len(expected)} expected, "
          f"{false_positives} false-positive, {len(unexplained)} unexplained)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
