"""LLM narrative pass for already-flagged stuck episodes.

`stuck.py` decides *whether* something counts as stuck, purely from
structured signals (question/score/hint state) — deterministic, testable,
never reads screen content. This module only runs *after* that decision:
one Ollama call per already-flagged finding, given that finding's own
verified facts plus the real screen text from its time window, to describe
*what* the student was struggling with (the grammar point, word, or
question content) — something the rules can't see because they never look
at content, only structure.

The gate matters: a hallucinated detail can embellish an episode the rules
already confirmed as stuck, but it can't invent a struggling student who
was fine, because this never runs on stretches the rules didn't flag.

Deliberately text, not images — vision models were tried and dropped for
this whole project already (FINDINGS.md): single-label bias, high latency,
and specifically failing on bilingual (Japanese/English) screens. The text
pipeline already solves that; feeding images back in here would reopen a
closed problem for no benefit.
"""
from __future__ import annotations

from datetime import datetime

import requests

from classify_recent_activity import OLLAMA_API, OLLAMA_MODEL, dedup_text

_SINGLE_ASK = (
    "Write ONE short sentence for a teacher describing what this specific "
    "question was testing — the sentence, missing word, or grammar point — "
    "based on the screen text above. The text is raw OCR: words may run "
    "together or repeat across near-duplicate frames, and there's leftover "
    "browser chrome mixed in — read past that noise for the actual question "
    "and hint text, the same way a person skimming a messy screenshot would. "
    "Hedge if you're not fully sure (\"appears to test ...\"), but give your "
    "best reading rather than declining."
)

_STREAK_ASK = (
    "This stretch covers several different questions in a row, not one — "
    "don't claim a single unifying grammar point. Write ONE short sentence "
    "for a teacher summarizing what kind of content this run of questions "
    "covered (topics, sentence patterns, or a couple of examples), based on "
    "the screen text above. The text is raw OCR: words may run together or "
    "repeat across near-duplicate frames, and there's leftover browser "
    "chrome mixed in — read past that noise, the same way a person skimming "
    "a messy screenshot would. Hedge if you're not fully sure, but give "
    "your best reading rather than declining."
)

NARRATE_PROMPT = (
    "A rule-based system has already confirmed a student got stuck during a "
    "practice drill, based on these verified facts. Do not contradict them "
    "and do not add facts that aren't listed here:\n{facts}\n\n"
    "Here is the text visible on the student's screen during this stretch:\n"
    "{text}\n\n"
    "{ask}"
)


def _episode_captures(finding: dict, captures: list[dict]) -> list[dict]:
    """The subset of captures falling inside this finding's time window."""
    t0 = datetime.fromisoformat(finding["t_start"])
    t1 = datetime.fromisoformat(finding["t_end"])
    return [c for c in captures
            if t0 <= datetime.fromisoformat(c["timestamp"]) <= t1]


def _facts_block(finding: dict) -> str:
    qs = finding["questions"]
    where = qs[0] if len(qs) == 1 else f"{qs[0]} through {qs[-1]} ({len(qs)} questions)"
    if finding["hint_max"] >= 2:
        hint = "used the maximum available hint"
    elif finding["hint_max"] == 1:
        hint = "only the baseline hint was shown, not opened further"
    else:
        hint = "no hint was shown"
    outcome = ("the drill moved on afterwards" if finding["resolved"]
               else "still stuck when the recording ended")
    return (
        f"- Question(s) involved: {where}\n"
        f"- Time spent: ~{finding['dwell_s']}s\n"
        f"- Hint use: {hint}\n"
        f"- None of these questions were answered correctly during this stretch\n"
        f"- Outcome: {outcome}"
    )


# Chrome's own built-in text-selection context menu — a fixed browser
# feature, not something a user configures, unlike a bookmark or extension
# name. Safe to strip literally; everything else gets cropped by position.
_CHROME_UI_NOISE = ["Ask Gemini", "Highlight"]


def _crop_to_content(caps: list[dict], profile: dict) -> list[dict]:
    """Drop whatever precedes the profile's content-start anchor in each
    frame — browser furniture (tabs, bookmarks, extensions) cropped by
    position, not by naming what a given user happens to have, since that's
    user-specific and can't be listed. Falls back to the untouched text if
    the profile has no content_start or the anchor isn't found."""
    start_fn = profile.get("content_start")
    out = []
    for c in caps:
        text = c["text"]
        if start_fn:
            idx = start_fn(text)
            if idx is not None:
                text = text[idx:]
        for noise in _CHROME_UI_NOISE:
            text = text.replace(noise, "")
        out.append({**c, "text": text})
    return out


def narrate_episode(finding: dict, captures: list[dict], profile: dict) -> str:
    """One Ollama call describing what a single already-flagged episode was
    about, using only that episode's own screen text. Deterministic decoding
    (same as classify()) so reruns are stable."""
    eps_caps = _crop_to_content(_episode_captures(finding, captures), profile)
    text = dedup_text(eps_caps)[:3000]
    if not text.strip():
        return "unclear from screen text"

    ask = _SINGLE_ASK if len(finding["questions"]) == 1 else _STREAK_ASK
    prompt = NARRATE_PROMPT.format(facts=_facts_block(finding), text=text, ask=ask)
    resp = requests.post(
        f"{OLLAMA_API}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "seed": 42},
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def add_narratives(findings: list[dict], captures: list[dict], profile: dict) -> int:
    """Mutate each finding in place, adding a 'narrative' key. Returns how
    many were added. Only call this on findings detect_stuck already
    produced — never on a whole session's worth of captures."""
    for f in findings:
        f["narrative"] = narrate_episode(f, captures, profile)
    return len(findings)
