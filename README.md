# Activity Classifier

A small, local-first tool that looks at what's on your screen and classifies
what you're likely doing — writing, coding, reading, and so on — using text
your computer already extracted, the page URL when you're in a browser, and
a small language model running entirely on your own machine. No cloud
calls, no screenshots sent anywhere, nothing to sign up for.

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
   doesn't expose accessibility text. It also records per-frame metadata:
   the app and window name, and the browser URL when you're in a browser.
2. This project asks screenpipe's local API for the text and metadata
   captured over a given window of time.
3. That text — not a screenshot — plus the page URL(s) is handed to a
   small model running locally via [Ollama](https://ollama.com), which
   classifies the activity into one of a fixed set of labels.

Pass `--reocr` to add a second OCR pass over the frame images
(`scripts/ocr_provider.py`), for when screenpipe's own OCR misses text —
notably non-Latin scripts (its 0.4.50 build returns English only for mixed
Japanese/English screens). It uses the best local engine per platform
(Apple Vision on macOS; Windows OCR / Tesseract elsewhere), merges in the
lines screenpipe dropped, and falls back cleanly where no local backend is
available. Recommended for bilingual domains. See FINDINGS.md.

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
   # bilingual screens (e.g. a Japanese tutor's feedback): add --reocr
   python scripts/classify_recent_activity.py --minutes 5 --profile language_acquisition --reocr
   ```

This pulls the last N minutes of captured text, prints a preview of it,
reports a few plain behavioral signals (see below), and prints a single
classification label.

## Domains / profiles

The classifier doesn't hardcode one label set. What it classifies into —
and any extra context the model needs to do that well — lives in
[`scripts/profiles.py`](scripts/profiles.py) as a small dictionary per
field, selected with `--profile`:

```bash
python scripts/classify_recent_activity.py --profile language_acquisition
```

- `general` (default) — a small generic starting set: `writing, coding,
  reading, researching, communicating, browsing_entertainment, idle`.
- `language_acquisition` — the first real target domain: teachers of
  self-study language learners (e.g. EFL students using an AI chatbot)
  have visibility into what happens inside their own tools, but none into
  what a student does around them. Labels: `writing_practice,
  reading_feedback, grammar_practice, vocab_lookup, translation_practice,
  off_task_browsing, idle`.

A profile's `context` string can also tell the model how to read URLs for
that field (e.g. which path means which app). Adding a new field means
adding an entry to `profiles.py`, not touching the capture/classify
engine.

### On "stuck"

Earlier versions had a `confused_or_stuck` label. It was dropped: whether
a learner is stuck is a *temporal* question — same screen for a long time,
repeated failed attempts at one question, escalating hint use — not
something a single-frame classifier should compete over. A frame of a
learner re-reading feedback for the fifth time still shows *grammar
practice*; the struggle is only visible across the ordered sequence of
frames plus their timestamps. That sequence-level pass is future work; the
base labels come first.

## Behavioral signals

Alongside the model's label, each run also reports plain signals computed
directly from screenpipe's capture metadata — no model involved: how many
times the active app changed, the longest gap without a new capture, and
what fraction of captures were unchanged repeats. These are cheap and
domain-general, and are the raw material for the sequence-level "stuck"
analysis described above.

## Testing / reproducing

Re-running screenpipe sessions to test a prompt or profile change is slow.
Instead, freeze a recording window into a fixture once and iterate against
it in seconds:

```bash
# build a fixture from your own screenpipe recording
python scripts/build_fixture.py my_spec.json my_fixture.json

# run the classifier over a fixture, report per-segment accuracy
python tests/eval_fixture.py --fixture my_fixture.json --profile language_acquisition
```

`build_fixture.py` scrubs PII (OS user/host/home/full name, plus a
per-spec host anonymisation and an optional gitignored `scrub.local`).
`tests/fixtures/korero_bilingual.json` is a checked-in example.
`screenpipe search --content-type ocr --start … --end …` reads the local
DB directly, no daemon — handy for poking at captured data.

## Project layout

```
scripts/
  classify_recent_activity.py   entry point — classify a live or fixed time window
  profiles.py                   label sets + context per domain (add a field here)
  ocr_provider.py               optional per-platform OCR pass over saved frames
  build_fixture.py              freeze a screenpipe window into a test fixture
  model_comparison.py           the model comparison behind FINDINGS.md (older run)
  annotate_captures.py          stamps classifier output onto real captured screenshots
tests/
  eval_fixture.py               run the classifier over a fixture, report accuracy
  fixtures/                     checked-in example fixture + its build spec
FINDINGS.md                     methodology and results
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
