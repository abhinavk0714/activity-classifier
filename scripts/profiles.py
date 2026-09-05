"""
Domain profiles: label sets and extra classification context.

The capture/classify engine (classify_recent_activity.py) is domain-agnostic
— it doesn't know what "language acquisition" or "coding" mean. What it
classifies into, and what extra context the model needs to do that well, is
kept here instead, so a new field can be supported by adding a profile
below, not by editing the engine.

Add a new field: add a "labels" list and a "context" string (can be ""),
then run with --profile <your_key>.
"""

DEFAULT_PROFILE = "general"

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
            "confused_or_stuck",
        ],
        "context": "",
    },
    # First real deployment target: teachers of self-study language learners
    # (initially EFL students using Kōrero, a Kyoto University chatbot
    # platform) have good visibility into what happens inside their own
    # chatbots, but none into what a student does around them — switching to
    # a translator, going off-task, or getting stuck without ever sending a
    # message. That gap is what this profile targets. Labels are still
    # generic language-learning activities (not tied to any one platform's
    # feature names), so this profile should transfer to other EFL/L2 tools.
    "language_acquisition": {
        "labels": [
            "writing_practice",
            "reading_feedback",
            "grammar_practice",
            "vocab_lookup",
            "translation_practice",
            "off_task_browsing",
            "idle",
            "confused_or_stuck",
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
            "multiple-choice grammar drills. Use 'confused_or_stuck' when "
            "the learner appears to be repeating the same failed attempt, "
            "re-reading the same feedback without progressing, or "
            "otherwise stalled."
        ),
    },
}
