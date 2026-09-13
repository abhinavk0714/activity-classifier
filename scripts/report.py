"""Render a single self-contained HTML report from a fixture: a chronological
timeline of classified activity plus a detailed view of any stuck episodes
(rule-based facts, behavioral signals, and the LLM narrative), with raw OCR
text available per-episode behind a closed-by-default toggle.

This is a *viewer* for the real pipeline, not a reimplementation of it: every
number and sentence in the report comes from classify_recent_activity.classify(),
stuck.detect_stuck(), classify_recent_activity.add_episode_signals(), and
narrate.add_narratives() — the same functions tests/eval_fixture.py and
tests/eval_narrate.py already call. report.py only lays the results out.

Fixture ground truth (the "segment" field, stuck_episodes/not_stuck_episodes)
is used for nothing here — this reports what the pipeline actually produced,
not what it was supposed to produce. That's an eval script's job, not a demo.

Timeline granularity: the fixture only has ground-truth segment boundaries,
and echoing those back would make the report look like it's grading itself.
Instead this classifies fixed-length wall-clock windows (--window-seconds,
default 45s) across the whole session — the same thing
classify_recent_activity.py's main() does for a single --minutes window,
just repeated across a longer recording. Each window gets its own
classify() call over that window's own deduped text/URLs/signals.

    python scripts/report.py [--fixture PATH] [--profile NAME]
                              [--text-source screenpipe|ocr|merged]
                              [--window-seconds N] [--output PATH]
                              [--skip-narrate]

Requires Ollama running with the model from classify_recent_activity.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from classify_recent_activity import (  # noqa: E402
    add_episode_signals,
    browser_urls,
    classify,
    compute_signals,
    dedup_text,
    format_signals,
)
from narrate import add_narratives  # noqa: E402
from ocr_provider import merge_text  # noqa: E402
from profiles import PROFILES  # noqa: E402
from stuck import detect_stuck, episode_captures  # noqa: E402

DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "gretel_calibration.json"
DEFAULT_PROFILE = "language_acquisition"
DEFAULT_OUTPUT = ROOT / "reports" / "report.html"

# A small fixed categorical palette, assigned to a profile's labels by index
# (not by hash) so a report over the same profile always colors a given
# label the same way. Kept distinct from the semantic colors used for the
# stuck/resolved chips (rust/green) so "this is a category" never reads as
# "this is a warning."
_LABEL_HUES = [210, 265, 25, 160, 320, 45, 190, 350]


def frame_text(frame: dict, source: str) -> str:
    if source == "screenpipe":
        return frame["screenpipe_text"]
    if source == "ocr":
        return frame["ocr_text"]
    if source == "merged":
        return merge_text(frame["screenpipe_text"], frame["ocr_text"])
    raise ValueError(source)


def build_captures(fixture: dict, text_source: str) -> list[dict]:
    base = datetime(2000, 1, 1)
    return [{
        "text": frame_text(f, text_source),
        "timestamp": (base + timedelta(seconds=f["t_offset_s"])).isoformat(),
        "browser_url": f["browser_url"],
        "capture_trigger": f.get("capture_trigger", ""),
        "t_offset_s": f["t_offset_s"],
    } for f in fixture["frames"]]


def build_windows(captures: list[dict], window_seconds: int) -> list[list[dict]]:
    """Bucket captures into fixed-length wall-clock windows, in order."""
    buckets: dict[int, list[dict]] = {}
    for c in captures:
        idx = int(c["t_offset_s"] // window_seconds)
        buckets.setdefault(idx, []).append(c)
    return [buckets[k] for k in sorted(buckets)]


def fmt_time(seconds: float) -> str:
    s = round(seconds)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


def classify_windows(windows: list[list[dict]], profile: dict) -> list[dict]:
    """Run the real classify() once per window. Returns render-ready dicts."""
    out = []
    for caps in windows:
        text = dedup_text(caps)
        urls = browser_urls(caps)
        signals = compute_signals(caps)
        signals_text = format_signals(signals)
        label = classify(text, profile["labels"], profile["context"], signals_text, urls)
        out.append({
            "t_start": caps[0]["t_offset_s"],
            "t_end": caps[-1]["t_offset_s"],
            "label": label,
            "urls": urls,
            "signals_text": signals_text,
            "frame_count": len(caps),
        })
    return out


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _label_classes(labels: list[str]) -> dict[str, int]:
    return {label: i % len(_LABEL_HUES) for i, label in enumerate(labels)}


def render_timeline(entries: list[dict], label_hue: dict[str, int]) -> str:
    rows = []
    for e in entries:
        hue = label_hue.get(e["label"], 0)
        span = f'{fmt_time(e["t_start"])}–{fmt_time(e["t_end"])}'
        urls = ", ".join(_esc(u) for u in e["urls"]) or "—"
        rows.append(f"""
        <li class="tl-row">
          <div class="tl-dot" style="--hue:{hue}"></div>
          <div class="tl-body">
            <div class="tl-head">
              <span class="tl-time">{span}</span>
              <span class="label-pill" style="--hue:{hue}">{_esc(e["label"])}</span>
              <span class="tl-frames">{e["frame_count"]} frame(s)</span>
            </div>
            <div class="tl-meta">url: {urls}</div>
            {f'<div class="tl-meta">{_esc(e["signals_text"])}</div>' if e["signals_text"] else ""}
          </div>
        </li>""")
    return "\n".join(rows)


def render_finding(i: int, f: dict, raw_text: str) -> str:
    resolved = f.get("resolved")
    resolved_chip = (
        '<span class="chip chip-resolved">moved on afterwards</span>' if resolved
        else '<span class="chip chip-unresolved">still stuck when last seen</span>'
    )
    sig = f.get("signals")
    sig_line = format_signals(sig) if sig else ""
    narrative = f.get("narrative", "").strip()

    return f"""
    <article class="episode">
      <header class="episode-head">
        <span class="flavour-badge">{_esc(f["flavour"])}</span>
        <h3>Stuck episode {i}: question(s) {_esc(f["question"] if len(f["questions"]) == 1 else f'{f["questions"][0]}–{f["questions"][-1]}')}</h3>
        {resolved_chip}
      </header>

      <div class="facts">
        <div class="facts-label">Rule-based facts (deterministic)</div>
        <p class="facts-summary">{_esc(f["summary"])}</p>
        <div class="facts-grid">
          <div><span class="k">dwell</span><span class="v">{f["dwell_s"]}s</span></div>
          <div><span class="k">frames</span><span class="v">{f["frames"]}</span></div>
          <div><span class="k">max hint level</span><span class="v">{f["hint_max"]}</span></div>
        </div>
        {f'<p class="facts-signals">{_esc(sig_line)}</p>' if sig_line else ""}
      </div>

      <div class="narrative">
        <div class="narrative-label">AI narrative &mdash; interpretation, not verified</div>
        <p class="narrative-text">{_esc(narrative)}</p>
      </div>

      <details class="raw-toggle">
        <summary>show raw screen text for this episode</summary>
        <pre class="raw-text">{_esc(raw_text)}</pre>
      </details>
    </article>"""


CSS = """
:root {
  --bg: #f5f6f8;
  --surface: #ffffff;
  --surface-alt: #eef0f3;
  --text: #1c2128;
  --text-dim: #5b6472;
  --border: #dbdfe4;
  --accent: #35507a;
  --mono-bg: #eef0f3;
  --mono-border: #d4d9e0;
  --narrative-bg: #fbf3e3;
  --narrative-border: #e4cfa0;
  --stuck: #a8461f;
  --stuck-soft: #fbe9e0;
  --resolved: #276749;
  --resolved-soft: #e3f3ea;
  --unresolved: #a8461f;
  --unresolved-soft: #fbe9e0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14171c;
    --surface: #1b1f26;
    --surface-alt: #20242c;
    --text: #e7e9ec;
    --text-dim: #9aa1ac;
    --border: #2b3038;
    --accent: #8fb1dd;
    --mono-bg: #20242c;
    --mono-border: #343b46;
    --narrative-bg: #2c2415;
    --narrative-border: #4a3c22;
    --stuck: #e08a5c;
    --stuck-soft: #3a2a20;
    --resolved: #6fbd93;
    --resolved-soft: #1e3327;
    --unresolved: #e08a5c;
    --unresolved-soft: #3a2a20;
  }
}
:root[data-theme="dark"] {
  --bg: #14171c;
  --surface: #1b1f26;
  --surface-alt: #20242c;
  --text: #e7e9ec;
  --text-dim: #9aa1ac;
  --border: #2b3038;
  --accent: #8fb1dd;
  --mono-bg: #20242c;
  --mono-border: #343b46;
  --narrative-bg: #2c2415;
  --narrative-border: #4a3c22;
  --stuck: #e08a5c;
  --stuck-soft: #3a2a20;
  --resolved: #6fbd93;
  --resolved-soft: #1e3327;
  --unresolved: #e08a5c;
  --unresolved-soft: #3a2a20;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  line-height: 1.55;
}
.wrap { max-width: 880px; margin: 0 auto; padding: 2.5rem 1.5rem 5rem; }

h1, h2, h3 {
  font-family: Georgia, "Iowan Old Style", "Times New Roman", serif;
  text-wrap: balance;
  font-weight: 600;
  margin: 0 0 0.3em;
}
h1 { font-size: 1.9rem; }
h2 { font-size: 1.3rem; margin-top: 2.5rem; padding-bottom: 0.4rem; border-bottom: 1px solid var(--border); }
h3 { font-size: 1.05rem; margin: 0; }

header.report-head {
  border-bottom: 1px solid var(--border);
  padding-bottom: 1.4rem;
  margin-bottom: 1rem;
}
.report-meta { color: var(--text-dim); font-size: 0.9rem; }
.report-meta div { margin-top: 0.15rem; }

.stat-strip {
  display: flex;
  gap: 1.5rem;
  flex-wrap: wrap;
  margin-top: 1.2rem;
}
.stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.6rem 1rem;
  min-width: 8rem;
}
.stat .n {
  display: block;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-variant-numeric: tabular-nums;
  font-size: 1.4rem;
  color: var(--accent);
}
.stat .l { color: var(--text-dim); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; }

/* timeline */
ul.timeline { list-style: none; margin: 1rem 0 0; padding: 0; position: relative; }
ul.timeline::before {
  content: "";
  position: absolute;
  left: 5px;
  top: 6px;
  bottom: 6px;
  width: 2px;
  background: var(--border);
}
.tl-row { position: relative; padding: 0 0 1.1rem 1.8rem; }
.tl-dot {
  position: absolute;
  left: 0;
  top: 0.35rem;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: hsl(var(--hue) 55% 45%);
  border: 2px solid var(--bg);
}
.tl-head { display: flex; align-items: baseline; gap: 0.6rem; flex-wrap: wrap; }
.tl-time {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-variant-numeric: tabular-nums;
  color: var(--text-dim);
  font-size: 0.88rem;
}
.label-pill {
  background: hsl(var(--hue) 45% 93%);
  color: hsl(var(--hue) 55% 28%);
  border: 1px solid hsl(var(--hue) 40% 78%);
  border-radius: 999px;
  padding: 0.1rem 0.65rem;
  font-size: 0.82rem;
  font-weight: 600;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .label-pill {
    background: hsl(var(--hue) 35% 20%);
    color: hsl(var(--hue) 65% 82%);
    border-color: hsl(var(--hue) 30% 34%);
  }
}
:root[data-theme="dark"] .label-pill {
  background: hsl(var(--hue) 35% 20%);
  color: hsl(var(--hue) 65% 82%);
  border-color: hsl(var(--hue) 30% 34%);
}
.tl-frames { color: var(--text-dim); font-size: 0.82rem; }
.tl-meta {
  color: var(--text-dim);
  font-size: 0.82rem;
  margin-top: 0.15rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  overflow-x: auto;
  white-space: nowrap;
}

/* stuck episodes */
.episode {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.2rem 1.3rem;
  margin: 1.1rem 0;
}
.episode-head { display: flex; align-items: center; gap: 0.7rem; flex-wrap: wrap; margin-bottom: 0.8rem; }
.episode-head h3 { flex: 1 1 auto; min-width: 12rem; }
.flavour-badge {
  border: 1px solid var(--border);
  color: var(--text-dim);
  border-radius: 999px;
  padding: 0.1rem 0.6rem;
  font-size: 0.76rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.chip {
  border-radius: 999px;
  padding: 0.15rem 0.7rem;
  font-size: 0.8rem;
  font-weight: 600;
  white-space: nowrap;
}
.chip-resolved { background: var(--resolved-soft); color: var(--resolved); }
.chip-unresolved { background: var(--unresolved-soft); color: var(--unresolved); }

.facts {
  background: var(--mono-bg);
  border: 1px solid var(--mono-border);
  border-radius: 8px;
  padding: 0.9rem 1rem;
  margin-bottom: 0.8rem;
}
.facts-label {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  margin-bottom: 0.4rem;
}
.facts-summary {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.92rem;
  margin: 0 0 0.6rem;
}
.facts-grid { display: flex; gap: 1.4rem; flex-wrap: wrap; margin-bottom: 0.4rem; }
.facts-grid .k {
  display: block;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-dim);
}
.facts-grid .v {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-variant-numeric: tabular-nums;
  font-size: 0.95rem;
}
.facts-signals {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.8rem;
  color: var(--text-dim);
  margin: 0.3rem 0 0;
}

.narrative {
  background: var(--narrative-bg);
  border: 1px dashed var(--narrative-border);
  border-radius: 8px;
  padding: 0.9rem 1rem;
  margin-bottom: 0.6rem;
}
.narrative-label {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  margin-bottom: 0.4rem;
}
.narrative-text {
  font-family: Georgia, "Iowan Old Style", "Times New Roman", serif;
  font-style: italic;
  font-size: 1rem;
  margin: 0;
}

.raw-toggle summary {
  cursor: pointer;
  color: var(--accent);
  font-size: 0.85rem;
  user-select: none;
}
.raw-text {
  margin-top: 0.6rem;
  background: var(--mono-bg);
  border: 1px solid var(--mono-border);
  border-radius: 6px;
  padding: 0.8rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 22rem;
  overflow: auto;
  color: var(--text-dim);
}

.empty-note { color: var(--text-dim); font-style: italic; }

footer {
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
  color: var(--text-dim);
  font-size: 0.8rem;
}
"""


def render_html(fixture: dict, args: argparse.Namespace, timeline: list[dict],
                 findings: list[dict], captures: list[dict], profile: dict) -> str:
    label_hue = _label_classes(profile["labels"])
    total_span = captures[-1]["t_offset_s"] - captures[0]["t_offset_s"] if captures else 0

    timeline_html = render_timeline(timeline, label_hue)

    if findings:
        episodes_html = "\n".join(
            render_finding(
                i + 1, f,
                dedup_text(episode_captures(f, captures)),
            )
            for i, f in enumerate(findings)
        )
    else:
        episodes_html = '<p class="empty-note">No stuck episodes flagged in this recording.</p>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Activity Report &mdash; {_esc(args.fixture.stem)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="report-head">
    <h1>Activity classification report</h1>
    <div class="report-meta">
      <div>fixture: <strong>{_esc(args.fixture.name)}</strong> &mdash; {_esc(fixture.get("source", ""))}</div>
      <div>profile: {_esc(args.profile)} &middot; text source: {_esc(args.text_source)} &middot; window: {args.window_seconds}s</div>
    </div>
    <div class="stat-strip">
      <div class="stat"><span class="n">{fmt_time(total_span)}</span><span class="l">session length</span></div>
      <div class="stat"><span class="n">{len(timeline)}</span><span class="l">classified windows</span></div>
      <div class="stat"><span class="n">{len(findings)}</span><span class="l">stuck episodes flagged</span></div>
    </div>
  </header>

  <h2>Timeline</h2>
  <ul class="timeline">
{timeline_html}
  </ul>

  <h2>Stuck episodes</h2>
{episodes_html}

  <footer>
    Generated by scripts/report.py from the real pipeline
    (classify_recent_activity.classify / stuck.detect_stuck / narrate.add_narratives),
    not from this fixture's ground truth. Raw screen text is shown verbatim,
    OCR errors and all &mdash; it is not cleaned up for this report.
  </footer>
</div>
</body>
</html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ap.add_argument("--profile", default=DEFAULT_PROFILE, choices=list(PROFILES))
    ap.add_argument("--text-source", default="merged",
                     choices=["screenpipe", "ocr", "merged"])
    ap.add_argument("--window-seconds", type=int, default=45,
                     help="Fixed wall-clock window size for timeline classification.")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--skip-narrate", action="store_true",
                     help="Skip the LLM narrative pass (faster; facts/signals still shown).")
    args = ap.parse_args()

    fixture = json.loads(args.fixture.read_text())
    profile = PROFILES[args.profile]
    captures = build_captures(fixture, args.text_source)

    print(f"fixture: {args.fixture.name}  ({len(captures)} frames)")
    windows = build_windows(captures, args.window_seconds)
    print(f"classifying {len(windows)} window(s) of ~{args.window_seconds}s each...")
    timeline = classify_windows(windows, profile)

    findings = detect_stuck(captures, profile)
    print(f"detected {len(findings)} stuck episode(s)")
    if findings:
        add_episode_signals(findings, captures)
        if not args.skip_narrate:
            print("narrating flagged episodes...")
            add_narratives(findings, captures, profile)

    page = render_html(fixture, args, timeline, findings, captures, profile)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page)
    print(f"\nwrote {args.output}  ({len(page)} bytes)")


if __name__ == "__main__":
    main()
