"""
Step 6: Annotate real captured screenshots with the classifier's output.

Goal: a concrete, provably-real demo artifact — actual captured screenshots
with the model's classification stamped directly onto the image, not a
mockup. Pulls recent captures (screenshot file + already-extracted text),
classifies each with the local text model, and draws the result as a
banner across the top of a copy of the image.

Older captures get merged by screenpipe into per-monitor .mp4 files to save
space (the standalone screenshot file no longer exists on disk). For those,
this script pulls the matching still frame out of the video with ffmpeg
using the capture's offset_index, so older sessions can still be annotated.

Prerequisites:
- screenpipe running (`screenpipe record`) and reachable at localhost:3030
- Ollama running with the classifier model pulled (see classify_recent_activity.py)
- ffmpeg on PATH (only needed for captures that have been merged into video)

Run: python 06_annotate_captures.py [--minutes 5] [--limit 10]
   or: python 06_annotate_captures.py --start <iso> --end <iso> [--limit 50]
       to annotate a fixed, already-captured window instead of "recent".
Output: each run gets its own subfolder under ./annotated/, named after the
window, with an index.txt listing every frame's timestamp/app/label.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import requests
from PIL import Image, ImageDraw, ImageFont

SCREENPIPE_API = "http://localhost:3030"
OLLAMA_API = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:3b"
OUTPUT_DIR = "./annotated"

LABELS = [
    "writing",
    "coding",
    "reading",
    "researching",
    "communicating",
    "browsing_entertainment",
    "idle",
    "confused_or_stuck",
]

PROMPT_TEMPLATE = (
    "You are looking at text extracted from a user's screen (not the raw "
    "screenshot, just the text that was visible). "
    "Classify what the user was most likely doing into exactly one of these "
    "categories: {labels}. "
    "Respond with only the category name, nothing else.\n\n"
    "Screen text:\n{text}"
)

BANNER_COLOR = (20, 20, 20, 200)  # semi-transparent dark banner
TEXT_COLOR = (255, 255, 255, 255)
BANNER_HEIGHT_RATIO = 0.06  # banner height as a fraction of image height


def get_api_token() -> str:
    result = subprocess.run(
        ["./target/release/screenpipe", "auth", "token"],
        cwd="..", capture_output=True, text=True, check=True,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[-1]


def fetch_captures(token: str, minutes: int = None, limit: int = 10,
                    start: str = None, end: str = None):
    """Returns [(timestamp, file_path, app_name, text, offset_index), ...]
    for captures in the window that have a usable screenshot source — either
    a standalone image file, or (for older, compacted captures) a video file
    plus the frame offset within it."""
    if not (start and end):
        end = datetime.now(timezone.utc).isoformat()
        start = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    headers = {"Authorization": f"Bearer {token}"}
    params = {"start_time": start, "end_time": end, "limit": limit, "content_type": "ocr"}

    resp = requests.get(f"{SCREENPIPE_API}/search", headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    captures = []
    for item in reversed(resp.json().get("data", [])):  # API returns newest-first; walk chronologically
        c = item.get("content", {})
        text = (c.get("text") or "").strip()
        file_path = c.get("file_path")
        if text and file_path and os.path.exists(file_path):
            captures.append((c.get("timestamp"), file_path, c.get("app_name") or "?",
                              text, c.get("offset_index")))
    return captures


def resolve_frame_image(file_path: str, offset_index, tmp_dir: str) -> str:
    """Returns a path to a real image file for this capture. If file_path is
    already an image, returns it as-is. If it's a compacted video, extracts
    the matching frame with ffmpeg into tmp_dir and returns that path."""
    if not file_path.lower().endswith(".mp4"):
        return file_path

    out_path = os.path.join(tmp_dir, f"frame_{offset_index}.jpg")
    subprocess.run(
        ["ffmpeg", "-y", "-i", file_path, "-vf", f"select=eq(n\\,{offset_index})",
         "-vframes", "1", "-q:v", "2", out_path],
        capture_output=True, text=True, check=True,
    )
    return out_path


def classify(text: str) -> str:
    prompt = PROMPT_TEMPLATE.format(labels=", ".join(LABELS), text=text[:6000])
    resp = requests.post(
        f"{OLLAMA_API}/api/generate",
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
              "options": {"temperature": 0, "seed": 42}},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def normalize_label(raw: str) -> str:
    """The model is asked to return exactly one label, but occasionally
    echoes screen text instead. Match against the known label set (falling
    back to a short, filesystem-safe slug) so a bad response can never leak
    raw screen content into a filename."""
    first_line = raw.splitlines()[0].strip().lower()
    for known in LABELS:
        if known in first_line:
            return known
    slug = "".join(c if c.isalnum() else "_" for c in first_line)[:30].strip("_")
    return slug or "unrecognized"


def annotate(image_path: str, label: str, timestamp: str, out_path: str):
    img = Image.open(image_path).convert("RGBA")
    w, h = img.size
    banner_h = max(40, int(h * BANNER_HEIGHT_RATIO))

    overlay = Image.new("RGBA", (w, banner_h), BANNER_COLOR)
    img.paste(overlay, (0, 0), overlay)

    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", size=int(banner_h * 0.4))
    except Exception:
        font = ImageFont.load_default()

    caption = f"classified: {label}   ({timestamp})"
    draw.text((16, banner_h * 0.25), caption, fill=TEXT_COLOR, font=font)

    img.convert("RGB").save(out_path, "JPEG", quality=90)


def _safe(s: str) -> str:
    return s.replace(":", "-").replace(" ", "_")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=5,
                         help="How many minutes of recent captures to pull")
    parser.add_argument("--limit", type=int, default=10,
                         help="Max number of captures to annotate")
    parser.add_argument("--start", type=str, default=None,
                         help="Fixed window start (ISO 8601). Use with --end to "
                              "annotate an already-captured session instead of "
                              "'recent' captures.")
    parser.add_argument("--end", type=str, default=None,
                         help="Fixed window end (ISO 8601). See --start.")
    parser.add_argument("--name", type=str, default=None,
                         help="Name for this run's output subfolder. Defaults "
                              "to the time window.")
    args = parser.parse_args()

    try:
        token = get_api_token()
    except Exception as e:
        print(f"Could not get API token — is screenpipe built and set up? ({e})")
        sys.exit(1)

    captures = fetch_captures(token, minutes=args.minutes, limit=args.limit,
                               start=args.start, end=args.end)
    if not captures:
        window = f"{args.start} to {args.end}" if args.start else f"last {args.minutes} minute(s)"
        print(f"No captures with both text and a screenshot file found in "
              f"the window ({window}). Is `screenpipe record` running, and "
              f"do the original screenshot files still exist on disk?")
        sys.exit(1)

    if args.name:
        run_dir_name = _safe(args.name)
    elif args.start:
        run_dir_name = f"{_safe(args.start)}_{_safe(args.end)}"
    else:
        run_dir_name = _safe(datetime.now().isoformat(timespec="seconds"))

    run_dir = os.path.join(OUTPUT_DIR, run_dir_name)
    os.makedirs(run_dir, exist_ok=True)
    print(f"Annotating {len(captures)} capture(s) into {run_dir}/...\n")

    index_lines = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        for i, (timestamp, file_path, app_name, text, offset_index) in enumerate(captures):
            label = normalize_label(classify(text))
            out_name = f"{i:02d}_{label}.jpg"
            out_path = os.path.join(run_dir, out_name)
            try:
                source_image = resolve_frame_image(file_path, offset_index, tmp_dir)
                annotate(source_image, label, timestamp, out_path)
            except subprocess.CalledProcessError:
                print(f"[{timestamp}] {app_name:20s} -> {label:25s} (could not extract frame, skipped)")
                continue
            line = f"[{timestamp}] {app_name:20s} -> {label:25s} {out_name}"
            print(line)
            index_lines.append(line)

    with open(os.path.join(run_dir, "index.txt"), "w") as f:
        f.write("\n".join(index_lines) + "\n")

    print(f"\nDone. Annotated images + index.txt in {run_dir}/")


if __name__ == "__main__":
    main()
