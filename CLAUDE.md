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

## Tooling

Python 3, `venv`, `pip install -r requirements.txt`. No build step.

## Data

`annotated/` holds real screen-capture screenshots used as a ground-truth
reference set — personal screen content, not synthetic. Treat it as
sensitive; don't casually push it somewhere more public than this repo's
intended audience without checking with the user first.
