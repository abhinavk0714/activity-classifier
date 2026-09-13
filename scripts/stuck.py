"""Sequence-level "stuck" detection — a second pass over a time-ordered
run of frames, after the base classifier.

It does NOT emit a label (confused_or_stuck was deliberately dropped from
the profiles — see FINDINGS.md). It emits a short structured *story*:
"stuck on questions 1/10 through 3/10 (3 in a row) for ~1m30s, none
answered correctly, asked for more hints". Whether a frame carries a
question / score / hint state at all is domain-specific and comes from
the profile's extract_state(); the episode grouping and the stuck rule
here are domain-general.

Reference: the wheel-spinning work (Baker et al., building on Beck &
Gong) — two flavours of unproductive persistence, hint-reliant "gaming"
vs hint-avoidant guessing. We label the flavour when the hint state makes
it clear.

Design note (redesigned 2026-09-12 against a real human-driven Gretel
recording — see NOTES.md Priority 2 step 4): the original version gated
on wall-clock dwell on one question (>=90s). That assumed a stuck student
sits on one question; the real recording showed the opposite — Gretel
advances the question index on every submission, right or wrong, and a
skip option appears within ~20s of failed attempts. A genuinely stuck
stretch in real data looked like *rapid cycling through many questions*
(7 questions in under 2 minutes) with the score never moving, not one
long dwell. So the unit of analysis changed from "seconds on question X"
to "was question X ever answered correctly" (per-item outcome, inferred
from the cumulative score crossing a boundary — robust to sparse capture
missing the exact correct-answer frame), and a stuck episode is now a
*streak* of consecutive unresolved items rather than a single dwell.
"""
from __future__ import annotations

import re
from datetime import datetime


def _fmt_duration(seconds: float) -> str:
    s = round(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60:02d}s"


def _question_num(question: str | None) -> int | None:
    """'3/5' -> 3. None if unparseable."""
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", question or "")
    return int(m.group(1)) if m else None


def episode_captures(finding: dict, captures: list[dict]) -> list[dict]:
    """The full slice of captures spanning this finding's time window — not
    just the question-bearing frames detect_stuck grouped on, but everything
    in between (e.g. a brief tab-switch mid-stall). Shared by callers that
    need an episode's raw text/metadata (narrate.py) or that want plain
    behavioral signals scoped to just this stretch (compute_signals(),
    which lives in classify_recent_activity.py — this only slices the
    captures; it doesn't compute anything itself, so it doesn't need that
    module's other domain-specific bits, avoiding a circular import)."""
    t0 = datetime.fromisoformat(finding["t_start"])
    t1 = datetime.fromisoformat(finding["t_end"])
    return [c for c in captures
            if t0 <= datetime.fromisoformat(c["timestamp"]) <= t1]


def _group_items(captures: list[dict], extract_state) -> list[dict]:
    """Group consecutive frames sharing the same (question, url) into one
    "item attempt". Frames with no question are ignored (they neither
    extend nor break a group), so a quick detour to a menu screen doesn't
    split a genuine stall."""
    items: list[dict] = []
    current: dict | None = None

    for c in captures:
        state = extract_state(c.get("text", "")) or {}
        question = state.get("question")
        if not question:
            continue
        ts = datetime.fromisoformat(c["timestamp"])
        url = c.get("browser_url", "")
        # an empty url is usually a capture/page-transition blip, not a
        # real navigation — don't let it fracture an otherwise-continuous
        # run on the same question
        if not url and current and current["key"][0] == question:
            url = current["key"][1]
        key = (question, url)

        if current and current["key"] == key:
            current["frames"].append((ts, state))
        else:
            if current:
                items.append(current)
            current = {"key": key, "frames": [(ts, state)]}

    if current:
        items.append(current)

    stats = []
    for it in items:
        frames = it["frames"]
        question, url = it["key"]
        scores = [st["score"] for _, st in frames if "score" in st]
        hints = [st["hint_level"] for _, st in frames if "hint_level" in st]
        stats.append({
            "question": question,
            "url": url,
            "t_start": frames[0][0],
            "t_end": frames[-1][0],
            "frames": len(frames),
            "score_start": scores[0] if scores else None,
            "score_end": scores[-1] if scores else None,
            "hint_max": max(hints) if hints else 0,
        })
    return stats


def _resolve(item: dict, nxt: dict | None) -> bool | None:
    """Was this item answered correctly? True/False, or None if there's no
    score signal at all to judge by (neither flags nor breaks a streak).

    Primary evidence: score rising within the item itself. Fallback: score
    at the *start of the next item* being higher than this item's own
    score — catches a correct-answer frame sparse capture missed, without
    crediting this item for score gained by a later, unrelated one. Only
    used when the next item is a genuine continuation (its question number
    is higher than this one's) — a lower/equal number means the drill
    restarted, which resets the score too, so it isn't a valid comparison.
    """
    s0, s1 = item["score_start"], item["score_end"]
    if s0 is not None and s1 is not None and s1 > s0:
        return True

    if nxt is not None:
        qnum, nqnum = _question_num(item["question"]), _question_num(nxt["question"])
        sequential = qnum is not None and nqnum is not None and nqnum > qnum
        if sequential and nxt["score_start"] is not None:
            baseline = s1 if s1 is not None else s0
            if baseline is not None:
                return nxt["score_start"] > baseline

    if s0 is None and s1 is None:
        return None
    return False


def _streaks(items: list[dict]) -> list[list[dict]]:
    """Group consecutive unresolved items into streaks. A streak breaks on
    a correct answer, an inconclusive item (no score evidence either way),
    or the question index not increasing (a drill restart) — none of
    those should be blamed on the item before them."""
    streaks: list[list[dict]] = []
    current: list[dict] = []
    prev_qnum: int | None = None

    for i, item in enumerate(items):
        qnum = _question_num(item["question"])
        sequential = prev_qnum is not None and qnum is not None and qnum > prev_qnum
        if current and not sequential:
            streaks.append(current)
            current = []

        nxt = items[i + 1] if i + 1 < len(items) else None
        outcome = _resolve(item, nxt)
        if outcome is False:
            current.append(item)
        elif current:
            streaks.append(current)
            current = []

        prev_qnum = qnum

    if current:
        streaks.append(current)
    return streaks


def _summarise(streak: list[dict], has_more_after: bool) -> dict:
    t0, t1 = streak[0]["t_start"], streak[-1]["t_end"]
    questions = [it["question"] for it in streak]
    hint_max = max(it["hint_max"] for it in streak)

    return {
        "question": questions[0],
        "questions": questions,
        "url": streak[0]["url"],
        "t_start": t0.isoformat(),
        "t_end": t1.isoformat(),
        "dwell_s": round((t1 - t0).total_seconds()),
        "frames": sum(it["frames"] for it in streak),
        "hint_max": hint_max,
        # the drill moved on afterwards (correctly, by skip, or by restart)
        # vs. the recording ended while still on this streak
        "resolved": has_more_after,
    }


def _is_stuck(a: dict) -> bool:
    # any escalated hint use on an unresolved item is real evidence; absent
    # that, need more than one question in a row to rule out a single hard
    # question that would've been solved with one more try
    return a["hint_max"] >= 2 or len(a["questions"]) >= 2


def _summary(a: dict) -> tuple[str, str]:
    dur = _fmt_duration(a["dwell_s"])
    qs = a["questions"]
    where = (f"question {qs[0]}" if len(qs) == 1
             else f"questions {qs[0]} through {qs[-1]} ({len(qs)} in a row)")

    if a["hint_max"] >= 2:
        flavour = "hint-reliant"
        hint_clause = "asked for more hints"
    elif a["hint_max"] <= 1:
        flavour = "hint-avoidant"
        hint_clause = "never opened a hint"
    else:
        flavour = "unproductive"
        hint_clause = ""

    parts = [f"stuck on {where} for ~{dur}", "none answered correctly"]
    if hint_clause:
        parts.append(hint_clause)
    parts.append("moved on afterwards" if a["resolved"] else "still stuck when last seen")
    return flavour, "; ".join(parts)


def detect_stuck(captures: list[dict], profile: dict) -> list[dict]:
    """Return a list of stuck-episode findings (possibly empty). Each is a
    structured dict with a human-readable 'summary'. No-op for profiles
    without an extract_state (nothing to group on)."""
    extract_state = profile.get("extract_state")
    if not extract_state or len(captures) < 2:
        return []

    items = _group_items(captures, extract_state)
    streaks = _streaks(items)

    findings = []
    for i, streak in enumerate(streaks):
        has_more_after = streak[-1] is not items[-1] if items else False
        a = _summarise(streak, has_more_after)
        if _is_stuck(a):
            flavour, summary = _summary(a)
            a["flavour"] = flavour
            a["summary"] = summary
            findings.append(a)
    return findings


def format_findings(findings: list[dict]) -> str:
    """Renders each finding's rule-based summary, plus a 'signals:' line if
    the caller attached one (classify_recent_activity.py's
    add_episode_signals) and a 'detail:' line if it attached a narrative
    (narrate.py's add_narratives) — both purely descriptive, computed
    elsewhere; stuck.py only displays them here if present, so both stay
    optional and this remains the single place finding output is rendered."""
    if not findings:
        return ""
    lines = []
    for f in findings:
        lines.append(f"  - [{f['flavour']}] {f['summary']}")
        sig = f.get("signals")
        if sig:
            apps = ", ".join(sig["distinct_apps"])
            lines.append(
                f"      signals: {sig['app_switches']} app switch(es) among "
                f"[{apps}]; {sig['repeat_ratio']:.0%} unchanged-repeat frames"
            )
        if f.get("narrative"):
            lines.append(f"      detail: {f['narrative']}")
    return "\n".join(lines)
