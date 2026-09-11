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

Backends (first available wins, in this order per platform):
    macOS    Apple Vision, automaticallyDetectsLanguage=True    (no install)
    Windows  Windows.Media.Ocr (pip install winsdk) -> RapidOCR -> Tesseract
    Linux    RapidOCR (pip install rapidocr-onnxruntime) -> Tesseract

Non-macOS notes:
  - Windows.Media.Ocr recognizes whatever language packs are installed on
    the machine; add Japanese under Settings > Language for JA support.
  - RapidOCR ships an English+Chinese recognition model. For Japanese,
    point OCR_RAPIDOCR_REC_MODEL at a japan_PP-OCRv*_rec ONNX model.
  - Tesseract needs the 'jpn' traineddata (e.g. apt install tesseract-ocr-jpn);
    without it this falls back to English only.

These non-macOS paths are written to each engine's documented API but
have not been exercised on a Windows/Linux box — the macOS path is the
only one covered by tests so far.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
from pathlib import Path

# Unicode ranges by script, for merge_text() and callers that want to know
# whether a frame even has non-Latin text worth a second OCR pass.
SCRIPT_RANGES = {
    "ja": r"぀-ヿ一-鿿",   # hiragana, katakana, CJK ideographs
    "ko": r"가-힯",
    "zh": r"一-鿿",
}

# Resolved (path -> str) OCR callable for the current platform, cached after
# the first successful selection so engine construction happens once.
_backend = None
_backend_name = "uninitialized"


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def engine_name() -> str:
    """Name of the backend that would be / is used. Cheap: does not build
    the engine, just reports what's available."""
    if _backend is not None:
        return _backend_name
    if sys.platform == "darwin":
        return "apple-vision"
    for name, ok in _fallback_candidates():
        if ok:
            return name
    return "none:not-available"


def _fallback_candidates() -> list[tuple[str, bool]]:
    """(name, available) for the non-macOS backends, in preference order."""
    win_media = sys.platform == "win32" and _have("winsdk")
    rapid = _have("rapidocr_onnxruntime") or _have("rapidocr")
    tess = _have("pytesseract") and shutil.which("tesseract") is not None
    ordered = []
    if sys.platform == "win32":
        ordered.append(("windows-media-ocr", win_media))
    ordered.append(("rapidocr", rapid))
    ordered.append(("tesseract", tess))
    return ordered


def ocr_image(path: str | Path) -> str:
    """OCR one image file. Returns recognized text (lines joined by \\n),
    or "" if the image can't be read. Raises NotImplementedError on
    platforms without any usable backend, ImportError if a chosen
    backend's deps are missing."""
    path = str(path)
    if sys.platform == "darwin":
        return _ocr_apple_vision(path)
    return _ocr_fallback(path)


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


# --- Windows / Linux -----------------------------------------------------

def _ocr_fallback(path: str) -> str:
    global _backend, _backend_name
    if _backend is None:
        _backend, _backend_name = _select_fallback()
    return _backend(path)


def _select_fallback():
    """Pick and build the best non-macOS backend once. Returns
    (callable, name); raises NotImplementedError if none are usable."""
    for name, ok in _fallback_candidates():
        if not ok:
            continue
        if name == "windows-media-ocr":
            return _ocr_windows_media, name
        if name == "rapidocr":
            return _make_rapidocr(), name
        if name == "tesseract":
            return _ocr_tesseract, name
    raise NotImplementedError(
        f"No OCR backend available for {sys.platform!r}. Install one of: "
        "winsdk (Windows), rapidocr-onnxruntime, or pytesseract + the "
        "tesseract binary with 'jpn' traineddata. See FINDINGS.md / "
        "the module docstring."
    )


def _make_rapidocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        from rapidocr import RapidOCR  # 2.x package rename

    rec_model = os.environ.get("OCR_RAPIDOCR_REC_MODEL")
    engine = RapidOCR(rec_model_path=rec_model) if rec_model else RapidOCR()

    def run(path: str) -> str:
        out = engine(path)
        # 1.x: (result, elapse) where result is [[box, text, score], ...] | None
        result = out[0] if isinstance(out, tuple) else getattr(out, "boxes", None)
        if not result:
            # 2.x result object exposes .txts
            txts = getattr(out, "txts", None)
            return "\n".join(txts) if txts else ""
        return "\n".join(item[1] for item in result)

    return run


def _ocr_tesseract(path: str) -> str:
    import pytesseract
    from PIL import Image

    with Image.open(path) as img:
        try:
            return pytesseract.image_to_string(img, lang="jpn+eng")
        except pytesseract.TesseractError:
            # 'jpn' traineddata missing — keep English rather than nothing.
            _warn_once("tesseract 'jpn' traineddata not found; English only")
            return pytesseract.image_to_string(img, lang="eng")


def _ocr_windows_media(path: str) -> str:
    import asyncio

    try:
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import FileAccessMode, StorageFile
    except ImportError as e:
        raise ImportError(
            "Windows OCR needs winsdk: pip install winsdk"
        ) from e

    async def _run() -> str:
        f = await StorageFile.get_file_from_path_async(str(Path(path).resolve()))
        stream = await f.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            engine = OcrEngine.try_create_from_language(Language("en-US"))
        if engine is None:
            _warn_once("no Windows OCR language pack available")
            return ""
        result = await engine.recognize_async(bitmap)
        return "\n".join(line.text for line in result.lines)

    return asyncio.run(_run())


_warned: set[str] = set()


def _warn_once(msg: str) -> None:
    if msg not in _warned:
        _warned.add(msg)
        print(f"ocr_provider: {msg}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: python {Path(sys.argv[0]).name} <image>")
        raise SystemExit(2)
    print(f"[{engine_name()}]")
    print(ocr_image(sys.argv[1]))
