"""Offline re-OCR of screenpipe's saved frames with Japanese enabled.

Why this exists: screenpipe 0.4.50's `apple-native` OCR path returns
English only, even when started with `--language japanese` — every
Japanese string on screen is dropped (see FINDINGS.md, 2026-09-07). The
macOS Vision framework itself handles Japanese fine when asked directly.
screenpipe keeps a per-frame JPEG (`frames.snapshot_path`), so the
Japanese can be recovered after the fact.

This is a standalone research tool, NOT part of the live pipeline — it's
slow (~1-2s/frame), needs the JPEGs to still be on disk, and is a stopgap
for a screenpipe bug. Use it to inspect Japanese content in a recording,
or to build a bilingual text set for a specific analysis.

Usage:
    python scripts/reocr_japanese.py <start_iso> <end_iso>
    python scripts/reocr_japanese.py 2026-09-07T20:53:00Z 2026-09-07T21:04:00Z

Prints, per frame: screenpipe's own OCR length vs. the re-OCR, and any
lines containing Japanese. Needs two extra packages not in
requirements.txt (they're only for this side tool, not the classifier):

    pip install pyobjc-framework-Vision pyobjc-framework-Quartz
"""
import re
import sqlite3
import sys
from pathlib import Path

import Quartz
import Vision
from Foundation import NSURL

DB = Path.home() / ".screenpipe" / "db.sqlite"
JP = re.compile(r"[぀-ヿ㐀-鿿]")  # hiragana, katakana, CJK ideographs


def ocr(path: str, languages: list[str]) -> str | None:
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        return None
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(languages)
    handler.performRequests_error_([req], None)
    lines = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if cand:
            lines.append(cand[0].string())
    return "\n".join(lines)


def main() -> None:
    start, end = sys.argv[1], sys.argv[2]
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT id, substr(timestamp,12,8), window_name, browser_url, "
        "snapshot_path, length(full_text) "
        "FROM frames WHERE timestamp BETWEEN ? AND ? AND snapshot_path IS NOT NULL "
        "ORDER BY timestamp",
        (start, end),
    ).fetchall()

    for fid, ts, win, url, snap, sp_len in rows:
        text = ocr(snap, ["ja-JP", "en-US"])
        if text is None:
            print(f"\n=== {fid} {ts} : image unreadable ({snap})")
            continue
        jp_lines = [ln for ln in text.split("\n") if JP.search(ln)]
        print(f"\n=== {fid} {ts} [{win}] {url or ''}")
        print(f"    screenpipe_ocr_chars={sp_len}  reocr_chars={len(text)}  jp_lines={len(jp_lines)}")
        for ln in jp_lines:
            print("    JP>", ln)


if __name__ == "__main__":
    main()
