"""Pure OCR demo — one file (PDF or image) -> markdown, via Mistral OCR. No RAG.

A single `client.ocr.process(...)` call, wrapped for three inputs (local PDF,
local image, remote URL), printing the recovered markdown.

    python examples/ocr_demo.py mydoc.pdf
    python examples/ocr_demo.py scan.png
    python examples/ocr_demo.py --url https://arxiv.org/pdf/2210.03629
    python examples/ocr_demo.py mydoc.pdf --out mydoc.md

Needs MISTRAL_API_KEY (loaded from cmbagent_lg/.env).
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)  # .env wins over a stale shell MISTRAL_API_KEY

DEFAULT_MODEL = "mistral-ocr-latest"
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff")


def _client():
    try:  # SDK 1.x
        from mistralai import Mistral
    except ImportError:  # SDK 2.x moved the client
        from mistralai.client import Mistral
    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        sys.exit("MISTRAL_API_KEY not set (add it to cmbagent_lg/.env).")
    return Mistral(api_key=key)


def ocr(path: str | None = None, url: str | None = None, model: str = DEFAULT_MODEL) -> str:
    """OCR a local PDF/image or a remote URL -> markdown string."""
    client = _client()

    if url:  # remote: hand Mistral the URL directly
        is_img = url.lower().split("?")[0].endswith(IMG_EXT)
        document = {"type": "image_url", "image_url": url} if is_img \
            else {"type": "document_url", "document_url": url}
    else:
        p = Path(path)
        if p.suffix.lower() in IMG_EXT:  # local image -> inline base64 data URI
            b64 = base64.b64encode(p.read_bytes()).decode()
            document = {"type": "image_url",
                        "image_url": f"data:image/{p.suffix.lstrip('.')};base64,{b64}"}
        else:  # local PDF -> upload, then OCR a signed URL (publishers block URL fetch)
            up = client.files.upload(
                file={"file_name": p.name, "content": p.read_bytes()}, purpose="ocr")
            signed = client.files.get_signed_url(file_id=up.id, expiry=1)
            document = {"type": "document_url", "document_url": signed.url}

    resp = client.ocr.process(model=model, document=document, include_image_base64=False)
    return "\n\n".join(page.markdown for page in resp.pages)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="local PDF or image file")
    ap.add_argument("--url", help="OCR a remote PDF/image URL instead of a local file")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Mistral OCR model id")
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
