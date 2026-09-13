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
a learner is stuck is a *sequence-level* question, not something a
single-frame classifier should compete over. A frame of a learner
re-reading feedback for the fifth time still shows *grammar practice*; the
struggle is only visible across an ordered run of frames.

That sequence-level pass is `scripts/stuck.py`'s `detect_stuck()` — purely
structural, no model involved. It reads whatever a profile's
`extract_state()` pulls out per frame (a question id, a score, a hint
level) and looks for *unresolved* items: was this question ever credited,
inferred from the cumulative score crossing a boundary. Consecutive
unresolved items form a streak; a streak is flagged as stuck on escalated
hint use or on 2+ unresolved items in a row. Calibrated against a real
human-driven recording, after an earlier wall-clock-dwell version turned
out to assume the wrong thing (that a stuck student sits on one question;
the real app advances the question on every submission, right or wrong).

On top of that, `scripts/narrate.py` adds an opt-in (`--narrate-stuck`)
LLM pass: one Ollama call *per already-flagged finding*, given that
finding's verified facts plus the real screen text from its time window,
to describe *what* the student was struggling with — something the rules
can't see, since they never read content. The gate matters: this never
runs on stretches the rules didn't already confirm as stuck, so a
hallucinated detail can embellish a real episode but can't invent one.
Text, not images, for the same reason the base classifier avoids vision
models (see above). Browser furniture (tabs, bookmarks, extensions) is
cropped by *position* — everything before a profile's `content_start`
anchor — rather than by naming what a specific user's browser happens to
contain.

## Behavioral signals

Alongside the model's label, each run also reports plain signals computed
directly from screenpipe's capture metadata — no model involved: how many
times the active app changed, the longest gap without a new capture, and
what fraction of captures were unchanged repeats. These are cheap and
domain-general, and are the raw material for the sequence-level "stuck"
analysis described above. The same signals also get computed scoped to
just one stuck episode's own time window (did the learner tab away to
something else mid-stall?), attached to that episode rather than only
reported once for the whole session.

## Viewing results

Terminal output isn't a great way to actually look at results — especially
the narrative pass (see "On stuck" above), which is worth reading, not
just grepping through logs for. `scripts/report.py` renders a single
self-contained HTML file from a fixture: a chronological timeline of
classified activity, plus a detailed view of any stuck episodes — the
rule-based facts and behavioral signals in one block, the LLM narrative in
a clearly separate one (it's never presented as equally verified), with
each episode's raw OCR text available behind a closed-by-default toggle
if you want to see how noisy the real input actually is.

```bash
python scripts/report.py --fixture my_fixture.json
# writes reports/report.html — open it in a browser
```

Classifies fixed-length windows across the whole recording rather than
echoing back the fixture's ground-truth segments — this shows what the
pipeline actually produced, not a graded answer key. `--skip-narrate`
skips the LLM pass for a fast dry run.

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
Fixtures live under `tests/fixtures/`, which is gitignored — they're built
from a real recording of whatever site/app you're testing against, so
they're kept local rather than checked in. Build your own with
`build_fixture.py` against a live screenpipe recording.
`screenpipe search --content-type ocr --start … --end …` reads the local
DB directly, no daemon — handy for poking at captured data.

## Project layout

```
scripts/
  classify_recent_activity.py   entry point — classify a live or fixed time window
  profiles.py                   label sets + context per domain (add a field here)
  ocr_provider.py               optional per-platform OCR pass over saved frames
  build_fixture.py              freeze a screenpipe window into a test fixture
  stuck.py                      sequence-level "stuck" detection (see "On stuck" below)
  narrate.py                    opt-in LLM narrative pass over already-flagged episodes
  report.py                     render a fixture's results as a self-contained HTML report
  model_comparison.py           the model comparison behind FINDINGS.md (older run)
  annotate_captures.py          stamps classifier output onto real captured screenshots
tests/
  eval_fixture.py               run the classifier over a fixture, report accuracy
  eval_stuck.py                 run the stuck-detection pass over a fixture, report accuracy
  eval_narrate.py               print the narrative pass's output over a fixture's findings
  fixtures/                     gitignored — build your own fixture + spec locally
reports/                        gitignored — generated by report.py, real captured content
FINDINGS.md                     methodology and results
requirements.txt
LICENSE
```

`annotated/`, a set of real captured screenshots used as a ground-truth
reference set for manual spot-checking, is kept locally and isn't included
in this repo — screen captures are personal by nature. `reports/` is the
same treatment, for the same reason: a generated report is built from a
real recording, not synthetic data.

## Status

An early-stage personal research project, not a production tool. The label
set and prompt are a starting point, not a final answer.

## License

MIT — see [LICENSE](LICENSE).
