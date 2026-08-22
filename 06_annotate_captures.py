"""
Step 6: Annotate real captured screenshots with the classifier's output.

Goal: a concrete, provably-real demo artifact — actual captured screenshots
with the model's classification stamped directly onto the image, not a
mockup. Pulls recent captures (screenshot file + already-extracted text),
classifies each with the local text model, and draws the result as a
banner across the top of a copy of the image.

Prerequisites:
- screenpipe running (`screenpipe record`) and reachable at localhost:3030
- Ollama running with the classifier model pulled (see classify_recent_activity.py)

Run: python 06_annotate_captures.py [--minutes 5] [--limit 10]
Output: annotated copies written to ./annotated/
"""

import argparse
import os
import subprocess
import sys
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


def fetch_captures(token: str, minutes: int, limit: int):
    """Returns [(timestamp, file_path, app_name, text), ...] for the most
    recent captures that actually have a screenshot file on disk."""
    end = datetime.now(timezone.utc).isoformat()
    start = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    headers = {"Authorization": f"Bearer {token}"}
    params = {"start_time": start, "end_time": end, "limit": limit, "content_type": "ocr"}

    resp = requests.get(f"{SCREENPIPE_API}/search", headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    captures = []
    for item in resp.json().get("data", []):
        c = item.get("content", {})
        text = (c.get("text") or "").strip()
        file_path = c.get("file_path")
        if text and file_path and os.path.exists(file_path):
            captures.append((c.get("timestamp"), file_path, c.get("app_name") or "?", text))
    return captures


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=5,
                         help="How many minutes of recent captures to pull")
    parser.add_argument("--limit", type=int, default=10,
                         help="Max number of captures to annotate")
    args = parser.parse_args()

    try:
        token = get_api_token()
    except Exception as e:
        print(f"Could not get API token — is screenpipe built and set up? ({e})")
        sys.exit(1)

    captures = fetch_captures(token, args.minutes, args.limit)
    if not captures:
        print(f"No captures with both text and a screenshot file found in the "
              f"last {args.minutes} minute(s). Is `screenpipe record` running?")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Annotating {len(captures)} capture(s)...\n")

    for i, (timestamp, file_path, app_name, text) in enumerate(captures):
        label = classify(text)
        out_path = os.path.join(OUTPUT_DIR, f"{i:02d}_{label}.jpg")
        annotate(file_path, label, timestamp, out_path)
        print(f"[{timestamp}] {app_name:20s} -> {label:25s} saved: {out_path}")

    print(f"\nDone. Annotated images in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
