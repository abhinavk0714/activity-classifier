# CLAUDE.md

Local, offline activity classifier for screenpipe. Research project — a
demo/showcase now, a study later. Not commercial, no roadmap pressure.

## What this depends on

- A running [screenpipe](https://screenpipe.com) instance at
  `localhost:3030` (installed separately — `npx screenpipe record` — never
  built from source here).
- [Ollama](https://ollama.com) at `localhost:11434` for local inference.

This project never touches screenpipe's source; it only calls its local
REST API. No Rust, no Tauri, no monorepo conventions apply here.

## Model choice

Default model is `qwen2.5:3b` — see `FINDINGS.md` for why (`llama3.2:3b`
had a severe single-label bias; `phi3.5` frequently ignored the closed
label set). Changing the default model should come with a re-run of the
comparison in `scripts/model_comparison.py`, not just a swap.

## Domain profiles

Label sets and per-field classification context live in
`scripts/profiles.py`, not in the engine (`classify_recent_activity.py`).
A new field is a new dict entry there, not an engine change. Keep it that
way — the goal is one general-purpose engine usable across many fields,
not a tool hardcoded to whichever field is being optimized for right now.

Within a profile, keep the label count similar to what's already there
(~8). `FINDINGS.md` documents small local models degrading — bias toward
one label, or ignoring the label set entirely — and that risk gets worse,
not better, with more labels. Resist the urge to add labels for every
nuance a domain expert wants captured; that's what behavioral signals
(below) are for instead.

## Behavioral signals

`compute_signals()` derives plain, model-free signals from capture
metadata (app switches, idle gaps, repeated/unchanged captures). This
exists because some real distinctions — e.g. "stuck re-reading the same
content" vs. "reading" — look identical in the text alone but are obvious
from timing/repetition. Prefer extending this over adding finer-grained
labels when a domain needs to distinguish flavors of "stuck" or "idle."

## Tooling

Python 3, `venv`, `pip install -r requirements.txt`. No build step.

## Data

`annotated/` holds real screen-capture screenshots used as a ground-truth
reference set — personal screen content, not synthetic. Treat it as
sensitive; don't casually push it somewhere more public than this repo's
intended audience without checking with the user first.
