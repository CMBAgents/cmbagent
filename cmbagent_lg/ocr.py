"""OCR a PDF to markdown + figures, via Mistral OCR.

`ocr_pdf_to_dir(pdf, out_dir)` uploads a LOCAL pdf, OCRs it with Mistral, and
writes `{out_dir}/document.md` plus each page's figures (`img-*.jpeg`) next to
it, so the markdown's `![img-0.jpeg](img-0.jpeg)` references resolve. No local
text extraction — OCR all the way down (headings, tables, math are preserved).

We upload the file and OCR a *signed URL* rather than handing Mistral a public
URL, because publishers (Nature, etc.) block Mistral's URL fetcher. The result
is cached: a second call with an existing `document.md` returns immediately and
does not re-pay.

Requires the optional `mistralai` dependency and `MISTRAL_API_KEY`:

    pip install 'cmbagent_lg[ocr]'
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

DEFAULT_OCR_MODEL = "mistral-ocr-latest"


def ocr_pdf_to_dir(
    pdf: str | Path,
    out_dir: str | Path,
    model: str = DEFAULT_OCR_MODEL,
    *,
    overwrite: bool = False,
) -> tuple[str, int]:
    """Mistral OCR on a local `pdf` → `{out_dir}/document.md` + figures.

    Args:
        pdf: path to a local PDF file.
        out_dir: directory to write `document.md` and `img-*` figures into.
        model: Mistral OCR model id.
        overwrite: re-OCR even if `document.md` already exists (default: use cache).

    Returns:
        `(markdown, n_figures)`.

    Raises:
        ImportError: the optional `mistralai` dependency is not installed.
        RuntimeError: `MISTRAL_API_KEY` is not set.
    """
    pdf = Path(pdf)
    out_dir = Path(out_dir)
    md_path = out_dir / "document.md"

    if md_path.exists() and not overwrite:  # cached — don't re-OCR / re-pay
        return md_path.read_text(), len(list(out_dir.glob("img-*")))

    try:
        # The SDK moved the client in 2.x: `from mistralai import Mistral` (1.x)
        # → `from mistralai.client import Mistral` (2.x, a namespace package with
        # no top-level re-export). Support both; the OCR/files API is identical.
        try:
            from mistralai import Mistral  # 1.x
        except ImportError:
            from mistralai.client import Mistral  # 2.x
    except ImportError as e:
        raise ImportError(
            "OCR needs the optional 'mistralai' dependency. "
            "Install it with:  pip install 'cmbagent_lg[ocr]'"
        ) from e

    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        raise RuntimeError(
            "MISTRAL_API_KEY is not set. Add it to your .env (see .env.example)."
        )

    client = Mistral(api_key=key)
    # Upload, then OCR a signed URL — publishers block Mistral's URL fetcher.
    up = client.files.upload(
        file={"file_name": pdf.name, "content": pdf.read_bytes()}, purpose="ocr"
    )
    signed = client.files.get_signed_url(file_id=up.id, expiry=1)
    resp = client.ocr.process(
        model=model,
        document={"type": "document_url", "document_url": signed.url},
        include_image_base64=True,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    n_fig = 0
    for page in resp.pages:
        for img in page.images or []:
            b64 = img.image_base64 or ""
            if "," in b64:  # strip the data: URI prefix
                b64 = b64.split(",", 1)[1]
            try:
                (out_dir / img.id).write_bytes(base64.b64decode(b64))
                n_fig += 1
            except Exception:  # noqa: BLE001 — skip an unparseable image, keep the rest
                pass

    md = "\n\n".join(p.markdown for p in resp.pages)
    md_path.write_text(md)
    return md, n_fig
