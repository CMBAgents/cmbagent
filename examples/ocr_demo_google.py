"""Pure OCR demo, Google edition — one file (PDF or image) -> markdown, via Gemini.

The `ocr_demo.py` job done with a general VLM instead of a purpose-built OCR
endpoint: one `client.models.generate_content(...)` call with the file and a
"transcribe to markdown" instruction, pinned at temperature 0 so it does not
paraphrase.

    python examples/ocr_demo_google.py scan.png
    python examples/ocr_demo_google.py mydoc.pdf
    python examples/ocr_demo_google.py --url https://arxiv.org/pdf/2210.03629
    python examples/ocr_demo_google.py mydoc.pdf --out mydoc.md --model gemini-3.6-flash

Needs GOOGLE_API_KEY (loaded from cmbagent_lg/.env).
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)  # .env wins over a stale shell key
os.environ.pop("GEMINI_API_KEY", None)  # avoid the "both keys set" notice on every client init

DEFAULT_MODEL = "gemini-3.6-flash"
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff")

PROMPT = (
    "Transcribe this document to clean GitHub-flavored Markdown. Preserve headings, "
    "tables, lists, and reading order; render any mathematics as LaTeX ($...$). "
    "Transcribe faithfully — do not summarise, translate, or add commentary. "
    "Output ONLY the transcription (no preamble, no surrounding code fence)."
)


def _client():
    from google import genai  # google-genai SDK
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("GOOGLE_API_KEY not set (add it to cmbagent_lg/.env).")
    return genai.Client(api_key=key)


def ocr(path: str | None = None, url: str | None = None, model: str = DEFAULT_MODEL) -> str:
    """OCR a local PDF/image or a remote URL -> markdown string, via Gemini."""
    from google.genai import types
    client = _client()

    if url:
        data = urllib.request.urlopen(url, timeout=60).read()
        mime = ("image/" + url.lower().split("?")[0].rsplit(".", 1)[-1]) if url.lower().split("?")[0].endswith(IMG_EXT) \
            else (mimetypes.guess_type(url)[0] or "application/pdf")
        file_part = types.Part.from_bytes(data=data, mime_type=mime)
    else:
        p = Path(path)
        mime = mimetypes.guess_type(str(p))[0] or (
            f"image/{p.suffix.lstrip('.')}" if p.suffix.lower() in IMG_EXT else "application/pdf")
        file_part = types.Part.from_bytes(data=p.read_bytes(), mime_type=mime)

    resp = client.models.generate_content(
        model=model,
        contents=[file_part, PROMPT],
        config=types.GenerateContentConfig(temperature=0),  # faithful, not creative
    )
    return (resp.text or "").strip()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="local PDF or image file")
    ap.add_argument("--url", help="OCR a remote PDF/image URL instead of a local file")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model id")
    ap.add_argument("--out", help="also write the markdown to this file")
    a = ap.parse_args()
    if not (a.path or a.url):
        ap.error("give a local file path or --url")

    md = ocr(a.path, a.url, a.model)
    print(md)
    if a.out:
        Path(a.out).write_text(md)
        print(f"\n[{len(md):,} chars -> {a.out}]", file=sys.stderr)


if __name__ == "__main__":
    main()
