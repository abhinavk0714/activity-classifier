"""Freeze a screenpipe recording window into a small JSON test fixture.

Iterating on the classifier by re-running screenpipe sessions is slow
(minutes). A fixture is captured once and then feeds `tests/eval_fixture.py`
in well under a second — no daemon, no re-capture.

Each fixture frame carries: relative timestamp, ground-truth segment label,
`browser_url`, `window_name`, `focused`, screenpipe's own OCR text, and a
fresh `ocr_provider` pass (which recovers non-Latin text screenpipe drops).

PII: text and URLs are scrubbed. Auto-removed: the OS user name, host
name, home directory, and the full name from the passwd GECOS field.
Add anything else to a gitignored `scrub.local` next to this script
(one `pattern<TAB>replacement` per line; pattern is a regex). The spec's
`scrub_replacements` handles host/domain anonymisation.

Usage:
    python scripts/build_fixture.py <spec.json> <out.json> [--no-ocr]

Spec format (see tests/fixtures/korero_bilingual.spec.json):
    {
      "db": "~/.screenpipe/db.sqlite",
      "source": "free-text description of the recording setup",
      "scrub_replacements": {"internal.host.example": "korero.example"},
      "segments": [
        {"label": "grammar_practice", "start": "<iso>", "end": "<iso>"},
        ...
      ]
    }
"""
from __future__ import annotations

import json
import os
import pwd
import re
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ocr_provider  # noqa: E402


def build_scrubber(spec_replacements: dict[str, str]):
    """Return a function str -> str applying all redactions."""
    subs: list[tuple[re.Pattern, str]] = []

    # OS-derived identity (never hard-coded here). Order matters: longer /
    # more-specific patterns first so e.g. the home dir is rewritten before
    # the bare user name inside it.
    pw = pwd.getpwuid(os.getuid())
    if pw.pw_dir:
        subs.append((re.compile(re.escape(pw.pw_dir)), "~"))
    host = socket.gethostname()
    for h in {host, host.split(".")[0], host.split("-")[0], host.split(".")[0].rstrip("s")}:
        if len(h) >= 4:
            subs.append((re.compile(rf"\b{re.escape(h)}\b", re.I), "host"))
    name_tokens = [t for t in re.split(r"[^A-Za-z]+", pw.pw_gecos) if len(t) >= 3]
    for i, tok in enumerate(name_tokens):
        subs.append((re.compile(rf"\b{re.escape(tok)}\b", re.I),
                     "Alex" if i == 0 else "Lee"))
    if pw.pw_name:
        subs.append((re.compile(rf"\b{re.escape(pw.pw_name)}\b", re.I), "user"))

    # Spec-provided (host/domain anonymisation), case-insensitive
    for pat, rep in (spec_replacements or {}).items():
        subs.append((re.compile(re.escape(pat), re.I), rep))

    # Optional local extras, gitignored
    local = Path(__file__).resolve().parent / "scrub.local"
    if local.exists():
        for line in local.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "\t" not in line:
                continue
            pat, rep = line.split("\t", 1)
            subs.append((re.compile(pat), rep))

    def scrub(text: str | None) -> str:
        text = text or ""
        for pat, rep in subs:
            text = pat.sub(rep, text)
        return text

    return scrub


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    spec_path, out_path = sys.argv[1], sys.argv[2]
    do_ocr = "--no-ocr" not in sys.argv[3:]

    spec = json.loads(Path(spec_path).read_text())
    db_path = os.path.expanduser(spec.get("db", "~/.screenpipe/db.sqlite"))
    segments = spec["segments"]
    # Only keep frames whose browser_url contains this substring — the
    # reliable way to exclude anything that isn't the site under test
    # (terminal, PDFs, other apps that happened to be on screen).
    url_filter = spec.get("url_filter", "")
    scrub = build_scrubber(spec.get("scrub_replacements", {}))

    window_start = min(s["start"] for s in segments)
    t0 = datetime.fromisoformat(window_start.replace("Z", "+00:00"))

    db = sqlite3.connect(db_path)
    frames = []
    n = 0
    for seg in segments:
        rows = db.execute(
            "SELECT timestamp, coalesce(browser_url,''), coalesce(window_name,''), "
            "coalesce(focused,0), coalesce(full_text,''), snapshot_path "
            "FROM frames WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (seg["start"].replace("Z", "+00:00"), seg["end"].replace("Z", "+00:00")),
        ).fetchall()
        for ts, url, win, focused, sp_text, snap in rows:
            if url_filter and url_filter not in url:
                continue
            n += 1
            ocr_text = ""
            if do_ocr and snap and os.path.exists(snap):
                try:
                    ocr_text = ocr_provider.ocr_image(snap)
                except (NotImplementedError, ImportError) as e:
                    print(f"  ocr skipped ({e.__class__.__name__}): {e}", file=sys.stderr)
                    do_ocr = False
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            frames.append({
                "n": n,
                "t_offset_s": round((dt - t0).total_seconds(), 1),
                "segment": seg["label"],
                "segment_note": seg.get("note", ""),
                "browser_url": scrub(url),
                "window_name": scrub(win),
                "focused": bool(focused),
                "screenpipe_text": scrub(sp_text),
                "ocr_text": scrub(ocr_text),
            })

    fixture = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "source": spec.get("source", ""),
        "ocr_engine": ocr_provider.engine_name() if do_ocr else "none",
        "note": "PII-scrubbed. 'segment' is the ground-truth label.",
        "segments": [{"label": s["label"], "note": s.get("note", "")} for s in segments],
        "frames": frames,
    }
    Path(out_path).write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out_path}: {len(frames)} frames across "
          f"{len({f['segment'] for f in frames})} segment label(s), "
          f"ocr_engine={fixture['ocr_engine']}")


if __name__ == "__main__":
    main()
