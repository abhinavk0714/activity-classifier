"""Sequence-level "stuck" detection — a second pass over a time-ordered
run of frames, after the base classifier.

It does NOT emit a label (confused_or_stuck was deliberately dropped from
the profiles — see FINDINGS.md). It emits a short structured *story*:
"stuck on question 3/5 for ~1m51s, score didn't move, asked for more
hints". Whether a frame carries a question / score / hint state at all is
domain-specific and comes from the profile's extract_state(); the episode
grouping and the stuck rule here are domain-general.

Reference: the wheel-spinning work (Baker et al., building on Beck &
Gong) — two flavours of unproductive persistence, hint-reliant "gaming"
vs hint-avoidant guessing. We label the flavour when the hint state makes
it clear.
"""
from __future__ import annotations

from datetime import datetime

# thresholds. Provisional — calibrated against a single deliberate
# wheel-spin episode in tests/fixtures/korero_bilingual (~111s, score
# stalled at 2, hint escalated Hint->More Hint). Revisit once there are
# contrasting ground-truthed sessions (NOTES.md Priority 2 step 4): the
# "long dwell, no escalation" path especially is a guess.
_MIN_DWELL_S = 90          # nothing shorter is worth a finding
_LONG_DWELL_S = 150        # long enough to flag even without hint escalation
_MIN_EPISODE_FRAMES = 3    # need a real run of frames on one question


def _fmt_duration(seconds: float) -> str:
    s = round(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60:02d}s"


def _episodes(captures: list[dict], extract_state) -> list[dict]:
    """Group consecutive frames sharing the same (question, url) into
    episodes. Frames with no question are ignored (they neither extend nor
    are counted), so a quick detour to a menu screen doesn't split a
    genuine stall."""
    episodes: list[dict] = []
    current: dict | None = None

    for c in captures:
        state = extract_state(c.get("text", "")) or {}
        question = state.get("question")
        if not question:
            continue
        ts = datetime.fromisoformat(c["timestamp"])
        key = (question, c.get("browser_url", ""))

        if current and current["key"] == key:
            current["frames"].append((ts, state))
        else:
            if current:
                episodes.append(current)
            current = {"key": key, "frames": [(ts, state)]}

    if current:
        episodes.append(current)
    return episodes


def _analyse(ep: dict) -> dict:
    frames = ep["frames"]
    question, url = ep["key"]
    t0, t1 = frames[0][0], frames[-1][0]
    dwell = (t1 - t0).total_seconds()

    scores = [st["score"] for _, st in frames if "score" in st]
    score_delta = (scores[-1] - scores[0]) if len(scores) >= 2 else None

    hints = [st["hint_level"] for _, st in frames if "hint_level" in st]
    hint_max = max(hints) if hints else 0
    hint_escalated = bool(hints) and hints[-1] > hints[0]

    return {
        "question": question,
        "url": url,
        "dwell_s": round(dwell),
        "frames": len(frames),
        "score_delta": score_delta,
        "hint_max": hint_max,
        "hint_escalated": hint_escalated,
    }


def _is_stuck(a: dict) -> bool:
    if a["frames"] < _MIN_EPISODE_FRAMES or a["dwell_s"] < _MIN_DWELL_S:
        return False
    # score present and moved forward -> making progress, not stuck
    if a["score_delta"] is not None and a["score_delta"] > 0:
        return False
    # a stalled score (delta == 0) is the core signal; without a visible
    # score we need the dwell to really drag before calling it
    if a["score_delta"] == 0:
        return a["hint_escalated"] or a["dwell_s"] >= _LONG_DWELL_S
    return a["dwell_s"] >= _LONG_DWELL_S


def _summary(a: dict) -> tuple[str, str]:
    dur = _fmt_duration(a["dwell_s"])
    if a["hint_escalated"]:
        flavour = "hint-reliant"
        hint_clause = "asked for more hints"
    elif a["hint_max"] <= 1:
        flavour = "hint-avoidant"
        hint_clause = "never opened a hint"
    else:
        flavour = "unproductive"
        hint_clause = ""

    score_clause = "score didn't move" if a["score_delta"] == 0 else "no score shown"
    parts = [f"stuck on question {a['question']} for ~{dur}", score_clause]
    if hint_clause:
        parts.append(hint_clause)
    return flavour, "; ".join(parts)


def detect_stuck(captures: list[dict], profile: dict) -> list[dict]:
    """Return a list of stuck-episode findings (possibly empty). Each is a
    structured dict with a human-readable 'summary'. No-op for profiles
    without an extract_state (nothing to group on)."""
    extract_state = profile.get("extract_state")
    if not extract_state or len(captures) < _MIN_EPISODE_FRAMES:
        return []

    findings = []
    for ep in _episodes(captures, extract_state):
        a = _analyse(ep)
        if _is_stuck(a):
            flavour, summary = _summary(a)
            a["flavour"] = flavour
            a["summary"] = summary
            findings.append(a)
    return findings


def format_findings(findings: list[dict]) -> str:
    if not findings:
        return ""
    return "\n".join(f"  - [{f['flavour']}] {f['summary']}" for f in findings)
