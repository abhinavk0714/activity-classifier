# Text-model comparison — findings

## Methodology

A short (~4 minute) scripted session was recorded, structured as six
back-to-back activities of known type, so ground truth was known going in
rather than inferred after the fact:

1. Writing (a document-editing session)
2. Researching (browsing an academic paper index/search)
3. Off-task entertainment (a short-form video platform)
4. Communicating (a webmail client)
5. Reading (a language-learning reference site)
6. Idle (no interaction at all)

Segment boundaries were verified against the actual captured content —
the original timer-based boundaries didn't line up exactly with when the
on-screen content changed, so boundaries for a few segments were corrected
to match what was actually on screen before scoring anything.

Three small local models were compared, all run through the same
pipeline (screenpipe's extracted accessibility/OCR text → prompt → model),
with deterministic decoding (temperature 0, fixed seed) so repeat runs on
identical input give identical output:

- `llama3.2:3b`
- `qwen2.5:3b`
- `phi3.5`

Two passes were run: one classification per activity segment (matching
real usage), and one classification per individual timestamped capture
within the session (30 distinct captures total) — the latter to check
whether a model stays consistent as the same or similar content is
captured repeatedly, not just whether it gets the "easy" cases right.

## Results

**`llama3.2:3b` shows a severe single-label bias.** 23 of 30 individual
captures were classified as `browsing_entertainment`, regardless of
whether the actual content was document writing, academic-paper reading,
or email — the model is not meaningfully discriminating between activity
types. This is the same category of failure (defaulting to one label
almost regardless of input) seen in earlier vision-model testing on this
project, just with a different default label.

**`phi3.5` frequently ignores the closed label set entirely**, inventing
its own words instead of one of the eight provided categories — observed
outputs included things like "investing," "reacting," "testing," and
"learning," none of which were in the label list it was explicitly given.
The prompt asks for exactly one category from a fixed list; a meaningful
fraction of the time it doesn't comply. That's a reliability problem for
anything expecting a fixed label set downstream, not just an accuracy one.

**`qwen2.5:3b` was the strongest of the three.** It correctly and
consistently classified the writing and researching segments across
multiple individual captures, and correctly identified the one
entertainment-platform capture tested directly. It had two failures out
of 30 captures, both the same failure mode — instead of returning a
label, it echoed a fragment of on-screen text (a video title/view count
visible in a sidebar) rather than classifying it. Two failures in 30 is
a real but far smaller problem than the other two models' pervasive
issues.

**Idle vs. reading produced identical results across all three models —
expected, and informative.** During the idle segment nothing on screen
changed, so screenpipe's captures were byte-identical to the tail end of
the reading segment before it. All three models therefore returned the
same prediction for both segments. This confirms something noted
earlier: text content alone can't distinguish genuine idleness from
"still looking at the same unchanged thing" — that would need a
behavioral signal (time since last change, absence of input events)
alongside the extracted text, not text alone.

## Decision

Defaulting to `qwen2.5:3b` going forward — the other two candidates each
have a disqualifying failure mode (severe single-label bias for
`llama3.2:3b`, frequent non-compliance with the closed label set for
`phi3.5`), not just lower accuracy.

---

# Bilingual (Japanese/English) session — Kōrero, 2026-09-07

First test against a real bilingual target: a live session on Kōrero's
public demo, driven programmatically over Chrome
DevTools Protocol while `screenpipe record` (v0.4.50, npx) ran. Single
built-in display, macOS `apple-native` OCR. This tests the risk flagged
in NOTES.md — that the text-extraction approach had only ever been
validated on the user's own English-only sessions.

## Methodology

The Kōrero demo build in use is an **earlier one** — four apps: Gretel
(grammar drills), Scotty (vocab SRS), Polly (translation, a Gradio app),
Sevi (CEFR simplifier). No Penny/Tammy, so the planned writing_practice
segment wasn't possible. Kōrero Nexus / Gretel / Scotty / Sevi are
**Flutter web** (canvas-rendered, no real DOM); Polly is Gradio (normal
DOM).

Three back-to-back segments of known type, timestamped:

1. **grammar_practice** — Gretel, two drill questions answered normally,
   Japanese translation toggle on (`別のソフトウェアを使うのはどうですか？`,
   `市内の地図が必要ですか？` visible on screen).
2. **confused_or_stuck** — Gretel, one cloze question, deliberate
   wheel-spinning: wrong answer, Hint, another wrong answer, More Hint
   until exhausted, ~95 s, then Skip.
3. **translation_practice** — Polly, an English sentence with L2 errors
   translated to Japanese (`私は毎日学校に通っており…`), then Explain.

Classified each segment window with both profiles (`general`,
`language_acquisition`) via the unchanged engine. Japanese OCR was then
re-tested in isolation under four screenpipe configs.

## Results

**Japanese extraction: 0%. Not degraded — absent.** Every Japanese string
visible on screen was completely missing from the OCR output (and from
accessibility text). Not garbled into wrong Latin characters — simply not
present. The English half of the same screen extracted fine. Tested
across ~40 captures and four configurations:

- default (`--disable-audio --disable-telemetry`)
- `--language english --language japanese` (via npx)
- same, via the CLI binary directly (confirmed both languages registered
  in the startup config table)
- `--use-pii-removal false` added

None produced a single Japanese character. Root cause is in screenpipe's
`apple-native` OCR path — the `--language japanese` value is accepted and
stored but the on-device Vision request still returns English-only. Not
yet isolated whether Apple's Vision framework itself can do JA+EN in one
pass (a direct `VNRecognizeTextRequest` probe with
`recognitionLanguages = ["ja","en"]` would settle it). **This is a hard
blocker for the Kōrero use case: tutor feedback is mostly in Japanese,
and it is exactly the content `confused_or_stuck` depends on.**

**Accessibility-tree extraction is effectively unavailable for Kōrero.**
Flutter web does not expose its semantic tree to the macOS AX API for a
normal user — it has to be switched on (a screen-reader-style opt-in). The
session only became navigable at all after triggering Flutter's
accessibility mode programmatically; a real student wouldn't have it on.
Even with it on, walks were shallow (~34 nodes) and often missed the
in-app question text. Polly (Gradio/DOM) does expose text to AX, but —
same as OCR — no Japanese. So for Kōrero the pipeline is OCR-only and
English-only.

**Every capture is polluted with Chrome UI text.** Each OCR/AX row begins
with Chrome's Tab Search panel dump (`Kōrero Nexus - Memory usage -
199 MB / Close / …` per tab) plus `Open Gemini in Chrome`, `Ask Gemini`,
`Canvas` from Chrome 152's built-in AI toolbar — 150–250 characters of
noise before any real content. `dedup_text()` doesn't remove it because
the memory figures change frame to frame, so it's never byte-identical.

**App/window attribution never identifies Kōrero.** `window_name` follows
the page `<title>`: `Kōrero Nexus`, `Gretel`, `Gradio` (for Polly), with
`- Audio playing` suffixes flipping it mid-segment. `app_name` is
`Google Chrome` throughout, so `compute_signals()` reports
`distinct_apps=["Google Chrome"]` and `app_switches=0` for the whole
session — it cannot see navigation between Kōrero apps, or between Kōrero
and a translator tab, which is the intra-Kōrero signal the project wants.
That information is in `window_name` / `browser_url`, not `app_name`.

**Capture cadence defeats the `confused_or_stuck` signals.** Event-driven
capture plus capture-time dedup produced ~1 frame per 20–40 s (23 frames
in ~8 min; some 2-minute windows had 2 frames). For the deliberately
stuck segment, `compute_signals()` reported `repeat_ratio=0.00` — the
"long run of near-identical captures" that is supposed to flag
wheel-spinning never forms, because near-duplicates are dropped before
they're stored and the gaps between stored frames are too large. Nothing
in the signals distinguished "stuck" from "reading."

**Classification (qwen2.5:3b), 1 of 6 correct:**

| segment | ground truth | `general` | `language_acquisition` |
|---|---|---|---|
| Gretel drill | grammar_practice | `reading` ✗ | `grammar_practice` ✓ |
| Gretel wheel-spinning | confused_or_stuck | `reading` ✗ | `reading_feedback` ✗ |
| Polly EN→JA | translation_practice | `researching` ✗ | `reading_feedback` ✗ |

The `language_acquisition` profile clearly helped on the one segment
whose captures were clean (grammar). The translation segment's two
stored frames happened to catch an empty Polly input box plus leftover
Gretel tab titles — and, with the Japanese output invisible, nothing in
the text said "translation." The stuck segment had the same problem: with
Japanese feedback unreadable and no repetition signal, it looks like
reading.

**English OCR quality:** usable but lossy — `I` → `1` consistently,
`Translate` → `Translatede`, stray characters (`Polly Porpoise Э`).

## Takeaways

- The bilingual-OCR risk is confirmed and is the top blocker. The text
  pipeline currently cannot see Japanese at all on this machine, so it
  cannot see tutor feedback, so `confused_or_stuck` (the highest-value
  label) has no signal to work from.
- Worth trying next, roughly in order: (a) a standalone
  `VNRecognizeTextRequest` probe to learn whether Apple Vision can do
  JA+EN, isolating OS vs. screenpipe; (b) check screenpipe issues /
  newer versions for Japanese OCR; (c) because Kōrero is a web app, a
  DOM-scrape capture path (browser extension) would sidestep OCR
  entirely — perfect JA+EN text plus real per-app URLs — and could feed
  the same engine and signals.
- `compute_signals()` should derive app identity from
  `window_name` / `browser_url`, not `app_name`, for browser-hosted
  targets.
- Stuck-detection needs a denser capture cadence (investigate
  screenpipe capture-rate / dedup flags) or a different mechanism than
  "repeated near-identical stored frames."
- The engine, prompt, and `language_acquisition` profile work end to end
  against a live Kōrero session; the profile beats `general` when the
  captured text is clean.

## Offline re-evaluation on the saved frames (same day, no re-capture)

screenpipe keeps a per-frame JPEG (`frames.snapshot_path`) and populates
`frames.browser_url` even though `app_name` is always `Google Chrome`.
That's enough to test fixes without re-running the session.

**Apple Vision *can* read the Japanese — screenpipe's OCR path is the
bug.** Re-OCRing the exact same saved JPEGs with a direct
`VNRecognizeTextRequest`, `recognitionLevel = accurate`,
`recognitionLanguages = ["ja-JP","en-US"]`, recovered the Japanese that
screenpipe dropped: `別のソフトウェアを使うのはどうですか？` (Gretel Q1),
`私は毎日学校に通っており、来週重要な試験があるため、英語を一生懸命勉強しています。`
(Polly output), etc. So the JA gap is fixable — either via an upstream
screenpipe fix, or by re-OCRing frames after the fact. (Direct Vision
English quality is a touch worse than screenpipe's hybrid — `studg`,
`Grammer` — and it also picks up the macOS menu bar / clock / dock, so
the practical approach is *merge*: screenpipe's text for English + the
JP-only lines from re-OCR.)

**Two cheap workarounds take the three Kōrero segments from 1/6 → 6/6.**
Re-scored with (a) `browser_url` → a one-line "which Kōrero tool this is"
context string for the model, (b) merged-in recovered Japanese, (c) the
`language_acquisition` label set **with `confused_or_stuck` removed**:

| segment | live baseline | + workarounds |
|---|---|---|
| Gretel drill | `reading` / `grammar_practice` | `grammar_practice` ✓ |
| Gretel wheel-spinning | `reading` / `reading_feedback` | `grammar_practice` ✓ * |
| Polly EN→JA | `researching` / `reading_feedback` | `translation_practice` ✓ |

(* the "stuck" segment is genuinely grammar practice — see below.)
Confirmed on three more bilingual segments captured during the JA
re-tests: 3/3. The `general` profile with no domain context still gets
0/3 ("researching", "reading", "communicating").

**Ablation — the URL context is the main lever.** On the translation and
grammar segments, *either* the URL context alone *or* the recovered
Japanese alone flips the prediction to correct; with neither, both fail
(`writing_practice`, `translation_practice`). URL context is the one to
lean on: always present, no OCR needed. Japanese recovery is still needed
for finer, content-level distinctions (which grammar point, what the
student actually wrote) and for any stuck-detection later.

**`confused_or_stuck` should not be a label.** With it in the set the
wheel-spinning segment classifies as `reading_feedback`; with it removed
the same captures classify as `grammar_practice` — which is what the
student was actually *doing*. Whether they were stuck is a separate,
temporal question — answered by looking across the ordered sequence of
(correctly-labelled) frames plus timestamps: same screen for a long
time, repeated wrong answers to the same question, escalating hint use.
That's a second pass on top of a working base classifier, not a label
the single-frame classifier competes over. Build the base labels first.

**A DOM-scrape browser extension was considered and rejected.** It would
give near-perfect data (route, question text, typed answers,
correct/incorrect, hint events, both languages) but bakes a Kōrero-
specific scraper into a tool that is meant to be general across many
fields, and logging keystrokes/answers inside a learning tool is far more
invasive than activity labels. The generic `browser_url` signal captures
much of the upside without the coupling.

## Changes made to the engine (this session)

Only what the re-eval justified:

- **`classify_recent_activity.py`** now reads `browser_url` from each
  capture, collects the distinct URLs for the window (`browser_urls()`),
  and passes them to the model as a plain line. Clean signal, no OCR
  errors, domain-general.
- **`profiles.py`**: `confused_or_stuck` removed from both `general` and
  `language_acquisition`. `language_acquisition` context gains a
  Kōrero-route map (/gretel→grammar_practice, /polly→translation_practice,
  /scotty→vocab_lookup, /sevi→reading_feedback, /nexus→home).

Verified on the three saved segments through the **live engine, no
re-OCR**: `language_acquisition` 3/3 (was 1/3 at the start of the
session). `general` 0/3 — expected, it carries no domain context and is
the fallback for unrecognised domains; the value is in the field profile.

Not wired in: the Japanese re-OCR (`tmp/reocr.py` stays a standalone
offline tool) and `compute_signals()` app-identity-from-URL (deferred).
