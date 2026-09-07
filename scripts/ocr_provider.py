"""Local OCR for screen frames, tuned for mixed-script UI text (e.g. a
Japanese tutor's feedback next to a student's English writing).

Why this exists: screenpipe's own OCR (0.4.50, event-driven capture path)
returns English only — it doesn't pass the configured languages to Apple
Vision, and even a correct language list drops Japanese on an
English-dominant screen unless auto-detect is used. See FINDINGS.md
("OCR layer — root cause and the fix"). This module re-OCRs the frame
JPEGs screenpipe already saves, using the best local engine per platform,
so the classifier sees the full bilingual text.

Public API:
    ocr_image(path) -> str     newline-joined recognized lines; "" on failure
    engine_name()   -> str     which backend is active (for logging)
    merge_text(base, extra, scripts=("ja",)) -> str
        keep `base` (e.g. screenpipe's good English + accessibility text)
        and append only the lines from `extra` that contain a target
        script — the practical way to add Japanese without regressing
        English.

Backends:
    macOS   Apple Vision, automaticallyDetectsLanguage=True    (no install)
    Windows Windows.Media.Ocr — built into Windows 10+, handles Japanese   (TODO)
    Linux   RapidOCR (pip install rapidocr-onnxruntime) or Tesseract
            with 'jpn'+'eng' language data                                (TODO)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Unicode ranges by script, for merge_text() and callers that want to know
# whether a frame even has non-Latin text worth a second OCR pass.
SCRIPT_RANGES = {
    "ja": r"぀-ヿ一-鿿",   # hiragana, katakana, CJK ideographs
    "ko": r"가-힯",
    "zh": r"一-鿿",
}


def engine_name() -> str:
    if sys.platform == "darwin":
        return "apple-vision"
    if sys.platform == "win32":
        return "windows-ocr:not-implemented"
    return "rapidocr/tesseract:not-implemented"


def ocr_image(path: str | Path) -> str:
    """OCR one image file. Returns recognized text (lines joined by \\n),
    or "" if the image can't be read. Raises NotImplementedError on
    platforms without a backend yet, ImportError if deps are missing."""
    path = str(path)
    if sys.platform == "darwin":
        return _ocr_apple_vision(path)
    raise NotImplementedError(
        f"No OCR backend for {sys.platform!r} yet. macOS uses Apple Vision. "
        "On Windows use Windows.Media.Ocr; on Linux use RapidOCR "
        "(pip install rapidocr-onnxruntime) or Tesseract with 'jpn'+'eng' "
        "language data. See FINDINGS.md."
    )


def has_script(text: str, script: str = "ja") -> bool:
    rng = SCRIPT_RANGES[script]
    return re.search(f"[{rng}]", text) is not None


def merge_text(base: str, extra: str, scripts: tuple[str, ...] = ("ja",)) -> str:
    """Return `base` with the lines of `extra` that contain any of `scripts`
    appended (deduplicated). Use when `base` has reliable Latin text and
    `extra` is a second OCR pass that also caught non-Latin lines."""
    ranges = "".join(SCRIPT_RANGES[s] for s in scripts)
    pat = re.compile(f"[{ranges}]")
    seen = set(line.strip() for line in base.splitlines())
    add = []
    for line in extra.splitlines():
        line = line.strip()
        # need >=2 script chars: single CJK glyphs are usually OCR'd UI icons
        if line and len(pat.findall(line)) >= 2 and line not in seen:
            add.append(line)
            seen.add(line)
    if not add:
        return base
    return base.rstrip() + "\n" + "\n".join(add)


# --- macOS: Apple Vision ---------------------------------------------------

def _ocr_apple_vision(path: str) -> str:
    try:
        import Quartz
        import Vision
        from Foundation import NSURL
    except ImportError as e:  # pragma: no cover - platform dependent
        raise ImportError(
            "Apple Vision OCR needs pyobjc: "
            "pip install pyobjc-framework-Vision pyobjc-framework-Quartz"
        ) from e

    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        return ""
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if cg is None:
        return ""

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    # Auto-detect is the only setting that keeps full English AND picks up
    # the Japanese lines on an English-dominant screen (FINDINGS.md sweep).
    req.setAutomaticallyDetectsLanguage_(True)

    if not handler.performRequests_error_([req], None):
        return ""
    lines = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if cand:
            lines.append(cand[0].string())
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: python {Path(sys.argv[0]).name} <image>")
        raise SystemExit(2)
    print(f"[{engine_name()}]")
    print(ocr_image(sys.argv[1]))
