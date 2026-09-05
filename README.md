# Activity Classifier

A small, local-first tool that looks at what's on your screen and classifies
what you're likely doing — writing, coding, reading, stuck, and so on —
using text your computer already extracted, and a small language model
running entirely on your own machine. No cloud calls, no screenshots sent
anywhere, nothing to sign up for.

> Built on top of **[screenpipe](https://screenpipe.com)**
> ([github.com/screenpipe/screenpipe](https://github.com/screenpipe/screenpipe)) —
> an open-source, local-first tool that continuously records your screen and
> audio and extracts searchable text from it via OS accessibility APIs and
> OCR. All of the capture and text-extraction work here is screenpipe's;
> this repo is a small classification layer built on top of the local API
> it exposes. Full credit to the screenpipe team for the underlying engine.

## How it works

1. [screenpipe](https://screenpipe.com) runs in the background, continuously
   capturing your screen and extracting text from it — first via the OS
   accessibility tree (fast, exact), falling back to OCR when a window
   doesn't expose accessibility text.
2. This project asks screenpipe's local API for the text captured over a
   given window of time.
3. That text — not a screenshot — is handed to a small model running
   locally via [Ollama](https://ollama.com), which classifies the activity
   into one of a fixed set of labels.

### Why text, not vision

It would be simpler to just feed screenshots to a vision-language model.
This deliberately doesn't, for two reasons: screenpipe has already turned
the screen into text more reliably than a vision model would re-derive it
from pixels, and a small text-only model is far cheaper and faster than a
vision model for what is ultimately a straightforward classification call.

## Setup

```bash
# Ollama, if not already installed
brew install ollama
ollama serve &
ollama pull qwen2.5:3b
```

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Running

1. Make sure screenpipe is recording:
   ```bash
   npx screenpipe record --disable-audio --disable-telemetry
   ```
   (`--disable-audio` since this classifier only uses screen text;
   `--disable-telemetry` to keep everything fully local.)
2. Make sure `ollama serve` is running with `qwen2.5:3b` pulled.
3. From the repo root:
   ```bash
   python scripts/classify_recent_activity.py --minutes 5
   ```

This pulls the last N minutes of captured text, prints a preview of it, and
prints a single classification label.

## Labels

A small, generic starting set:

```
writing, coding, reading, researching, communicating,
browsing_entertainment, idle, confused_or_stuck
```

`confused_or_stuck` is the most interesting one to validate — whether a
local model can pick up on struggle/confusion signals from screen text
alone was the open question this project set out to test.

## Project layout

```
scripts/
  classify_recent_activity.py   entry point — classify a live or fixed time window
  model_comparison.py           the model comparison behind FINDINGS.md
  annotate_captures.py          stamps classifier output onto real captured screenshots
FINDINGS.md                     model comparison methodology and results
requirements.txt
LICENSE
```

`annotated/`, a set of real captured screenshots used as a ground-truth
reference set for manual spot-checking, is kept locally and isn't included
in this repo — screen captures are personal by nature.

## Status

An early-stage personal research project, not a production tool. The label
set and prompt are a starting point, not a final answer.

## License

MIT — see [LICENSE](LICENSE).
