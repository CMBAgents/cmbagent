"""OCR engine comparison — Mistral OCR vs Gemini — on one PDF: text + figures.

Runs the same PDF through both paths and writes a `document.md` plus figure
images per engine. Mistral returns page markdown and figure bounding boxes;
Gemini returns text only, so figures are pulled separately with PyMuPDF.

    python examples/ocr_compare.py                       # default: ReAct arXiv:2210.03629
    python examples/ocr_compare.py --pdf mydoc.pdf
    python examples/ocr_compare.py --url https://arxiv.org/pdf/2210.03629 --workdir ~/Desktop/ocr_compare

Needs MISTRAL_API_KEY + GOOGLE_API_KEY (loaded from cmbagent_lg/.env). Outputs land
in <workdir>/{mistral,gemini}/ with a COMPARISON.md summary.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)  # .env wins over stale shell keys
os.environ.pop("GEMINI_API_KEY", None)

DEFAULT_URL = "https://arxiv.org/pdf/2210.03629"
GEMINI_MODEL = "gemini-3.6-flash"
PROMPT = (
    "Transcribe this entire document to clean GitHub-flavored Markdown. Preserve "
    "headings, tables, lists, and reading order; render mathematics as LaTeX ($...$). "
    "Where a figure/image appears, insert a line `![figure](figure)` as a placeholder. "
    "Transcribe faithfully across ALL pages — do not summarise or omit. "
    "Output ONLY the transcription (no preamble, no surrounding code fence)."
)


def download(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.write_bytes(urllib.request.urlopen(url, timeout=90).read())
    return dest


def _mistral():
    try:
        from mistralai import Mistral
    except ImportError:
        from mistralai.client import Mistral
    return Mistral(api_key=os.environ["MISTRAL_API_KEY"])


def run_mistral(pdf: Path, out: Path, dpi: int = 300) -> tuple[str, int, float]:
    """Mistral OCR text + hi-res figure crops.

    Mistral's own crops come back at its low page-render resolution, so we
    re-render each figure's bounding box from the PDF at `dpi` with fitz and
    rewrite the markdown img refs to the .png crops."""
    import fitz
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / "document.md"
    existing = sorted(out.glob("img-*.png"))
    if md_path.exists() and existing:  # cached
        return md_path.read_text(), len(existing), 0.0
    for old in out.glob("img-*"):  # clear any prior low-res jpegs / crops
        old.unlink()

    client = _mistral()
    t = time.time()
    up = client.files.upload(file={"file_name": pdf.name, "content": pdf.read_bytes()}, purpose="ocr")
    signed = client.files.get_signed_url(file_id=up.id, expiry=1)
    resp = client.ocr.process(
        model="mistral-ocr-latest",
        document={"type": "document_url", "document_url": signed.url},
        include_image_base64=False)  # we render crops ourselves, don't need the low-res base64

    md = "\n\n".join(p.markdown for p in resp.pages).replace(".jpeg", ".png")  # refs -> hi-res pngs
    md_path.write_text(md)

    doc = fitz.open(pdf)
    n = 0
    for pi, pg in enumerate(resp.pages):
        if not pg.images:
            continue
        page = doc.load_page(pi)
        ppp = 72.0 / (pg.dimensions.dpi or 96)  # OCR pixels -> PDF points
        for im in pg.images:
            rect = fitz.Rect(im.top_left_x * ppp, im.top_left_y * ppp,
                             im.bottom_right_x * ppp, im.bottom_right_y * ppp)
            if rect.width < 5 or rect.height < 5:
                continue
            try:
                name = im.id.rsplit(".", 1)[0] + ".png"  # img-0.jpeg -> img-0.png
                page.get_pixmap(clip=rect, dpi=dpi).save(out / name)
                n += 1
            except Exception:  # noqa: BLE001
                pass
    return md, n, time.time() - t


def run_gemini(pdf: Path, out: Path, model: str) -> tuple[str, float]:
    from google import genai
    from google.genai import types
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "document.md"
    if cache.exists():  # don't re-pay on re-runs
        return cache.read_text(), 0.0
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    part = types.Part.from_bytes(data=pdf.read_bytes(), mime_type="application/pdf")
    t = time.time()
    resp = client.models.generate_content(
        model=model, contents=[part, PROMPT],
        config=types.GenerateContentConfig(temperature=0, max_output_tokens=65536))
    md = (resp.text or "").strip()
    cache.write_text(md)
    return md, time.time() - t


def extract_figures_fitz(pdf: Path, out: Path) -> tuple[int, int]:
    """Companion extractor for the VLM path. Returns (embedded_rasters, rendered_pages).

    Vector figures (typical of arXiv) have no raster XObject, so we fall back to
    rendering the figure/table caption pages at 150 dpi."""
    import fitz
    for old in out.glob("img-*.png"):
        old.unlink()
    for old in out.glob("page-*.png"):
        old.unlink()
    doc = fitz.open(pdf)
    seen, raster = set(), 0
    for pno in range(doc.page_count):
        for img in doc.load_page(pno).get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n > 4:  # CMYK/alpha -> RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                pix.save(out / f"img-{raster:03d}.png")
                raster += 1
            except Exception:  # noqa: BLE001
                pass
    rendered = 0
    if raster == 0:  # vector figures: render the caption pages instead
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            if re.search(r"(?im)^\s*(figure|table)\s*\d", page.get_text()):
                page.get_pixmap(dpi=150).save(out / f"page-{pno + 1:03d}.png")
                rendered += 1
    return raster, rendered


def _words(t: str) -> list[str]:
    return re.sub(r"\s+", " ", t).strip().lower().split()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", help="local PDF (overrides --url)")
    ap.add_argument("--url", default=DEFAULT_URL, help="PDF URL to download (default: ReAct paper)")
    ap.add_argument("--workdir", default="~/Desktop/ocr_compare")
    ap.add_argument("--model", default=GEMINI_MODEL)
    ap.add_argument("--dpi", type=int, default=300, help="render DPI for hi-res figure crops")
    ap.add_argument("--engine", choices=["both", "mistral", "gemini"], default="both",
                    help="which OCR engine(s) to run")
    a = ap.parse_args()
    need_m = a.engine in ("both", "mistral")
    need_g = a.engine in ("both", "gemini")
    for k in (["MISTRAL_API_KEY"] if need_m else []) + (["GOOGLE_API_KEY"] if need_g else []):
        if not os.environ.get(k):
            sys.exit(f"{k} not set (add it to cmbagent_lg/.env).")

    wd = Path(a.workdir).expanduser()
    wd.mkdir(parents=True, exist_ok=True)
    pdf = Path(a.pdf).expanduser() if a.pdf else download(a.url, wd / "source.pdf")
    import fitz
    npages = fitz.open(pdf).page_count
    print(f"source: {pdf}  ({npages} pages)\n")

    if need_m:
        print("Mistral OCR …")
        m_md, m_fig, m_t = run_mistral(pdf, wd / "mistral", a.dpi)
        print(f"      {len(m_md):,} chars, {m_fig} hi-res figure crops @ {a.dpi}dpi, {m_t:.1f}s"
              + ("  (cached)" if m_t == 0.0 else ""))

    if need_g:
        print("Gemini …")
        g_md, g_t = run_gemini(pdf, wd / "gemini", a.model)
        g_raster, g_rendered = extract_figures_fitz(pdf, wd / "gemini")
        g_fig = g_raster or g_rendered
        g_kind = "embedded rasters" if g_raster else f"figure-page renders (vector PDF; {g_raster} embedded)"
        print(f"      {len(g_md):,} chars, {g_fig} images [{g_kind}], {g_t:.1f}s"
              + ("  (cached)" if g_t == 0.0 else ""))

    if a.engine != "both":  # single-engine run: report just that engine, no comparison
        if need_m:
            md, fig, t, tag, figs = m_md, m_fig, m_t, "Mistral OCR", "mistral/img-*.png (hi-res)"
        else:
            md, fig, t, tag, figs = g_md, g_fig, g_t, f"Gemini ({a.model})", "gemini/*.png"
        report = (f"# OCR — {pdf.name} ({npages} pages) — {tag}\n\n"
                  f"- transcribed: **{len(md):,} chars / {len(_words(md)):,} words**\n"
                  f"- figures: **{fig}** ({figs})\n"
                  f"- wall-clock: {t:.1f}s\n\n## First 500 chars\n```\n{md[:500]}\n```\n")
        (wd / "REPORT.md").write_text(report)
        print("\n" + "=" * 64)
        print(f"{tag}: {npages} pages | {len(md):,} chars / {len(_words(md)):,} words | "
              f"{fig} figures | {t:.1f}s")
        print(f"\noutputs -> {wd}/  (text: {'mistral' if need_m else 'gemini'}/document.md, "
              f"figures: {figs}, report: REPORT.md)")
        return

    mw, gw = _words(m_md), _words(g_md)
    inter = len(set(mw) & set(gw)); union = len(set(mw) | set(gw)) or 1
    jacc = inter / union
    ratio = difflib.SequenceMatcher(None, mw, gw).quick_ratio()

    report = f"""# OCR comparison — {pdf.name} ({npages} pages)

| metric | Mistral OCR | Gemini ({a.model}) |
|---|---|---|
| transcribed chars | {len(m_md):,} | {len(g_md):,} |
| transcribed words | {len(mw):,} | {len(gw):,} |
| figures extracted | {m_fig} hi-res crops @ {a.dpi}dpi (OCR bbox + PDF render) | {g_fig} ({g_kind}) |
| wall-clock | {m_t:.1f}s | {g_t:.1f}s |

**Text agreement:** word-set Jaccard = {jacc:.3f}; difflib quick_ratio = {ratio:.3f}
(1.0 = identical). Divergence is expected: OCR reads layout literally; the VLM
normalises wording and may truncate long docs at its output-token limit.

- Mistral text: `mistral/document.md`  ·  figures: `mistral/img-*.png` (hi-res)
- Gemini  text: `gemini/document.md`   ·  figures: `gemini/img-*.png`

## First 400 chars — Mistral
```
{m_md[:400]}
```

## First 400 chars — Gemini
```
{g_md[:400]}
```
"""
    (wd / "COMPARISON.md").write_text(report)
    print("\n" + "=" * 64)
    print(f"pages {npages} | figures: Mistral {m_fig} native crops vs Gemini {g_fig} {g_kind} | "
          f"words: {len(mw):,} vs {len(gw):,} | Jaccard {jacc:.3f} | ratio {ratio:.3f}")
    print(f"time: Mistral {m_t:.1f}s vs Gemini {g_t:.1f}s")
    print(f"\nfull report + outputs -> {wd}/COMPARISON.md")


if __name__ == "__main__":
    main()
