"""
Domain profiles: label sets and extra classification context.

The capture/classify engine (classify_recent_activity.py) is domain-agnostic
— it doesn't know what "language acquisition" or "coding" mean. What it
classifies into, and what extra context the model needs to do that well, is
kept here instead, so a new field can be supported by adding a profile
below, not by editing the engine.

Add a new field: add a "labels" list and a "context" string (can be ""),
then run with --profile <your_key>.

A profile may also define "extract_state": a function text -> dict pulling
structured per-frame state (a question number, a score, hint level) that
the sequence-level stuck pass groups frames by and watches for progress.
This is domain-specific screen-scraping, so it lives here, not the engine.

A profile with "extract_state" may also define "content_start": a function
text -> int | None returning where real content begins in the text (the
same anchor extract_state searches for), used by narrate.py to crop
browser furniture before it without having to name what a given user's
furniture contains (bookmarks, extensions — all user-specific).
"""

import re

DEFAULT_PROFILE = "general"

_QUESTION_RE = re.compile(r"Question\s+(\d+)\s*/\s*(\d+)")


def _language_acquisition_state(text: str) -> dict:
    """Pull the on-screen progress markers a grammar/vocab drill shows.
    Tuned to Kōrero's Gretel/Scotty UI; absent markers just come back None.
    'Question 3/5' + 'Correct: 2' stalling across frames = wheel-spinning."""
    state: dict = {}

    m = _QUESTION_RE.search(text)
    if m:
        state["question"] = f"{m.group(1)}/{m.group(2)}"

    m = re.search(r"Correct:?\s*(\d+)", text)
    if m:
        state["score"] = int(m.group(1))

    # the drill's hint button escalates Hint -> More Hint when the learner
    # asks again; "More Hint" showing means they've already asked once
    if "More Hint" in text:
        state["hint_level"] = 2
    elif "Hint" in text:
        state["hint_level"] = 1

    return state


def _language_acquisition_content_start(text: str) -> int | None:
    """Index where real question content begins, or None if this frame has
    none. Everything before it is browser furniture — tabs, bookmarks,
    toolbars — cropped generically by position rather than by naming
    whatever a given user happens to have (a bookmark bar entry, an
    extension icon) since that varies per user and can't be listed."""
    m = _QUESTION_RE.search(text)
    return m.start() if m else None

PROFILES = {
    "general": {
        "labels": [
            "writing",
            "coding",
            "reading",
            "researching",
            "communicating",
            "browsing_entertainment",
            "idle",
        ],
        "context": "",
    },
    # First real deployment target: teachers of self-study language learners
    # (initially EFL students using Kōrero, an AI chatbot suite) have good
    # visibility into what happens inside their own chatbots, but none into
    # what a student does around them — switching to a translator, going
    # off-task, or getting stuck without ever sending a message. That gap is
    # what this profile targets. Labels are still generic language-learning
    # activities (not tied to any one platform's feature names), so this
    # profile should transfer to other EFL/L2 tools.
    "language_acquisition": {
        "labels": [
            "writing_practice",
            "reading_feedback",
            "grammar_practice",
            "vocab_lookup",
            "translation_practice",
            "off_task_browsing",
            "idle",
        ],
        "context": (
            "This is a language learner using self-study tools, most likely "
            "an AI chatbot or app for practicing a second language (e.g. an "
            "EFL chatbot for a Japanese student practicing English). Notes: "
            "tutor/feedback text is often written in the learner's first "
            "language even though the learning target is a different "
            "language — text in a different language from the learner's "
            "own writing is usually tutor feedback, not off-task content, "
            "so don't classify it as browsing just because it's in another "
            "language. 'vocab_lookup' covers dictionaries, translation "
            "tools (e.g. Google Translate), or vocabulary drill apps. "
            "'grammar_practice' covers fill-in-the-blank, drag-and-drop, or "
            "multiple-choice grammar drills. "
            "On the initial pilot platform (Kōrero), the browser URL's path "
            "identifies the tool: a path segment /gretel is a grammar-drill "
            "app (grammar_practice); /polly is a translation tool "
            "(translation_practice); /scotty is vocabulary practice "
            "(vocab_lookup); /sevi simplifies reading passages "
            "(reading_feedback); /nexus is the app-chooser home screen."
        ),
        "extract_state": _language_acquisition_state,
        "content_start": _language_acquisition_content_start,
    },
}
