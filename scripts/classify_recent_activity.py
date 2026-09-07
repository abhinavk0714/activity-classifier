"""
Classify recent screen activity using already-extracted text, not vision.

Goal: query screenpipe's local search API for text captured in the last
few minutes (accessibility-tree text first, OCR as a fallback), then ask a
small local model (via Ollama) to classify what the user was likely doing.

This intentionally avoids feeding raw screenshots to a vision model — text
already extracted by screenpipe's own accessibility/OCR pipeline is more
reliable than re-deriving it from pixels, and a small local text model is
far cheaper and faster than a vision model for this kind of judgment call.

What labels exist and how the model is told to use them is domain-specific
(see profiles.py), not hardcoded here — pass --profile to switch fields.

When the captured activity is in a browser, the distinct page URLs seen in
the window are passed to the model too. A URL is clean (no OCR errors) and
often says more about the activity than the visible text — which site,
which app, whether the user switched to something off-task. Domain-general;
per-field profiles can add how to read specific URLs (see profiles.py).

--reocr re-runs OCR on each frame image with a local engine (Apple Vision
on macOS) and merges in text screenpipe's OCR dropped — notably non-Latin
scripts, which its 0.4.50 build skips entirely (see FINDINGS.md and
ocr_provider.py). Recommended for bilingual domains; falls back cleanly
where no local OCR backend is available.

Alongside the text-based label, this also reports plain behavioral signals
computed straight from screenpipe's capture metadata (app switches, idle
gaps, repeated/unchanged captures) — no model involved. These are cheap,
robust, and domain-general: they're what the model can miss (e.g. "stuck
re-reading the same feedback" looks a lot like "reading" in the text alone,
but shows up clearly as a long run of near-identical captures).

Prerequisites:
- screenpipe must already be running (`screenpipe record`) and reachable
  at http://localhost:3030
- Ollama must be running locally with a model pulled, e.g.:
    ollama pull qwen2.5:3b

Run (from the repo root): python scripts/classify_recent_activity.py [--minutes 5]
                       or: python scripts/classify_recent_activity.py --profile language_acquisition

For comparing variations (different label sets, prompts, models) against
the same underlying data rather than fresh live captures each time, use
--start/--end with a fixed, already-captured time window instead of
--minutes — this queries the same stored data every run:
    python scripts/classify_recent_activity.py --start 2026-08-19T00:08:50Z --end 2026-08-19T00:12:05Z
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests

import ocr_provider
from profiles import DEFAULT_PROFILE, PROFILES

SCREENPIPE_API = "http://localhost:3030"
OLLAMA_API = "http://localhost:11434"
# qwen2.5:3b — see FINDINGS.md; the other two candidates tested
# (llama3.2:3b, phi3.5) each had a disqualifying failure mode.
OLLAMA_MODEL = "qwen2.5:3b"

PROMPT_TEMPLATE = (
    "You are looking at text extracted from a user's screen over the last "
    "few minutes (not the raw screenshots, just the text that was visible)."
    "{context_block}"
    "{url_block}"
    "Classify what the user was most likely doing into exactly one of "
    "these categories: {labels}. "
    "{signals_block}"
    "Respond with only the category name, nothing else.\n\n"
    "Screen text:\n{text}"
)


def get_api_token() -> str:
    result = subprocess.run(
        ["npx", "--yes", "screenpipe", "auth", "token"],
        capture_output=True, text=True, check=True,
    )
    # stdout may include log lines; the token is the last non-empty line
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[-1]


def fetch_recent_captures(token: str, minutes: int = None, start: str = None, end: str = None) -> list[dict]:
    """Return captures in the window as dicts with timestamp/app_name/text,
    ordered oldest to newest. Preserves per-capture metadata (unlike a flat
    text blob) so behavioral signals can be computed from it."""
    if start and end:
        # fixed window — same data every call, for comparing variations fairly
        pass
    else:
        end = datetime.now(timezone.utc).isoformat()
        start = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "start_time": start,
        "end_time": end,
        "limit": 50,
    }

    captures = []
    for content_type in ("accessibility", "ocr"):
        resp = requests.get(
            f"{SCREENPIPE_API}/search",
            headers=headers,
            params={**params, "content_type": content_type},
            timeout=10,
        )
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            content = item.get("content", {})
            text = content.get("text", "").strip()
            if text:
                captures.append({
                    "timestamp": content.get("timestamp"),
                    "app_name": content.get("app_name") or "unknown",
                    "browser_url": content.get("browser_url") or "",
                    "file_path": content.get("file_path") or content.get("frame_name") or "",
                    "text": text,
                })
        if captures:
            # accessibility text is preferred; only fall back to OCR if empty
            break

    captures.sort(key=lambda c: c["timestamp"] or "")
    return captures


def dedup_text(captures: list[dict]) -> str:
    """Concatenate capture text, dropping consecutive near-identical
    captures (common with frequent polling) for the text handed to the model."""
    deduped = []
    for c in captures:
        if not deduped or c["text"] != deduped[-1]:
            deduped.append(c["text"])
    return "\n---\n".join(deduped)


def browser_urls(captures: list[dict], limit: int = 5) -> list[str]:
    """Distinct browser URLs seen in the window, most-seen first. A URL says
    a lot about the activity before any OCR text is read (which site, which
    app, whether the user switched to something off-task), and it's clean —
    no OCR errors. Domain-general: useful for any field, not just this one."""
    counts: dict[str, int] = {}
    for c in captures:
        url = c.get("browser_url") or ""
        if url:
            counts[url] = counts.get(url, 0) + 1
    return [u for u, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:limit]


def add_reocr(captures: list[dict]) -> int:
    """Re-OCR each frame image with a local engine and merge in the lines
    screenpipe's own OCR dropped — notably non-Latin scripts (its 0.4.50
    build returns English only for e.g. Japanese/English screens). Mutates
    each capture's "text" in place; returns how many were changed.

    Best-effort: on any backend problem (no macOS Vision, missing pyobjc)
    it warns once and leaves every capture untouched."""
    changed = 0
    for c in captures:
        path = c.get("file_path") or ""
        if not path or not os.path.exists(path):
            continue
        try:
            extra = ocr_provider.ocr_image(path)
        except (NotImplementedError, ImportError) as e:
            print(f"--reocr unavailable ({e.__class__.__name__}: {e}); "
                  f"using screenpipe's text as-is", file=sys.stderr)
            return 0
        merged = ocr_provider.merge_text(c["text"], extra)
        if merged != c["text"]:
            c["text"] = merged
            changed += 1
    return changed


def compute_signals(captures: list[dict]) -> dict:
    """Plain behavioral signals from capture metadata — no model involved.
    Cheap and domain-general: catches things the text alone tends to miss,
    like "stuck re-reading the same screen" looking identical to "reading"."""
    if len(captures) < 2:
        return {}

    timestamps = [datetime.fromisoformat(c["timestamp"]) for c in captures]
    apps = [c["app_name"] for c in captures]

    gaps = [(b - a).total_seconds() for a, b in zip(timestamps, timestamps[1:])]
    app_switches = sum(1 for a, b in zip(apps, apps[1:]) if a != b)
    repeats = sum(1 for a, b in zip(captures, captures[1:]) if a["text"] == b["text"])

    return {
        "duration_seconds": round((timestamps[-1] - timestamps[0]).total_seconds()),
        "capture_count": len(captures),
        "distinct_apps": sorted(set(apps)),
        "app_switches": app_switches,
        "longest_gap_seconds": round(max(gaps)) if gaps else 0,
        "repeat_ratio": round(repeats / (len(captures) - 1), 2),
    }


def format_signals(signals: dict) -> str:
    if not signals:
        return ""
    apps = ", ".join(signals["distinct_apps"])
    return (
        f"{signals['capture_count']} captures over {signals['duration_seconds']}s "
        f"across [{apps}] ({signals['app_switches']} app switch(es)); "
        f"longest gap without a new capture: {signals['longest_gap_seconds']}s; "
        f"{signals['repeat_ratio']:.0%} of consecutive captures were unchanged repeats"
    )


def classify(text: str, labels: list[str], context: str, signals_text: str,
             urls: list[str]) -> str:
    context_block = f" {context}" if context else ""
    url_block = (
        f" The user's browser was on: {', '.join(urls)}. " if urls else ""
    )
    signals_block = (
        f"Session signals (for context, not a label to output): {signals_text}. "
        if signals_text else ""
    )
    prompt = PROMPT_TEMPLATE.format(
        context_block=context_block,
        url_block=url_block,
        labels=", ".join(labels),
        signals_block=signals_block,
        text=text[:6000],
    )
    resp = requests.post(
        f"{OLLAMA_API}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            # deterministic decoding — same input must give the same output,
            # otherwise run-to-run variation gets wrongly blamed on the data
            "options": {"temperature": 0, "seed": 42},
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=5,
                         help="How many minutes of recent activity to classify")
    parser.add_argument("--start", type=str, default=None,
                         help="Fixed window start (ISO 8601). Use with --end to "
                              "query the same already-captured data every run, "
                              "instead of a shifting 'last N minutes' window.")
    parser.add_argument("--end", type=str, default=None,
                         help="Fixed window end (ISO 8601). See --start.")
    parser.add_argument("--profile", type=str, default=DEFAULT_PROFILE, choices=list(PROFILES),
                         help="Label set + classification context to use (see profiles.py).")
    parser.add_argument("--reocr", action="store_true",
                         help="Re-OCR each frame image locally and merge in text "
                              "screenpipe's OCR missed — notably non-Latin scripts "
                              "(its OCR returns English only). macOS uses Apple "
                              "Vision; adds ~0.3-1s per frame. Recommended for "
                              "bilingual domains (e.g. --profile language_acquisition).")
    args = parser.parse_args()

    profile = PROFILES[args.profile]

    try:
        token = get_api_token()
    except Exception as e:
        print(f"Could not get API token — is screenpipe built and set up? ({e})")
        sys.exit(1)

    captures = fetch_recent_captures(token, minutes=args.minutes, start=args.start, end=args.end)
    if not captures:
        window = f"{args.start} to {args.end}" if args.start else f"last {args.minutes} minute(s)"
        print(f"No screen text captured in the window ({window}). "
              f"Is `screenpipe record` running?")
        sys.exit(1)

    if args.reocr:
        n = add_reocr(captures)
        if n:
            print(f"(re-OCR merged extra text into {n}/{len(captures)} frame(s))\n")

    text = dedup_text(captures)
    signals = compute_signals(captures)
    signals_text = format_signals(signals)
    urls = browser_urls(captures)

    print(f"Captured text ({len(text)} chars):")
    print(text[:500] + ("..." if len(text) > 500 else ""))
    print()
    if urls:
        print(f"Browser URLs: {', '.join(urls)}")
        print()
    if signals_text:
        print(f"Session signals: {signals_text}")
        print()

    label = classify(text, profile["labels"], profile["context"], signals_text, urls)
    print(f"Classification ({args.profile}): {label}")


if __name__ == "__main__":
    main()
