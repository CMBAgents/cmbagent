"""OCR + RAG demo — turn a paper (and everything it cites) into a queryable vector DB.

Mistral OCR every PDF to markdown + figures, pull the citation list from
OpenAlex, download the open-access references, chunk and embed with
`text-embedding-3-small` into `sqlite-vec`, then KNN-retrieve and answer.

Everything is cached under --workdir (OCR is never repeated). Needs
MISTRAL_API_KEY (OCR) and OPENAI_API_KEY (embeddings + answer).

Usage:
  python examples/ocr_rag_demo.py --build --max-refs 45    # OCR paper + all OA refs, embed
  python examples/ocr_rag_demo.py --ask "How was subtype switching validated in vivo?"
  python examples/ocr_rag_demo.py --ask "..." --no-answer  # retrieval only
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import struct
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ── config / clients ─────────────────────────────────────────────────────

DEFAULT_DOI = "10.1038/s41588-025-02389-7"
EMBED_MODEL = "text-embedding-3-small"   # 1536-d
EMBED_DIM = 1536
MAILTO = "boris.bolliet@gmail.com"       # polite OpenAlex pool
UA = "ocr-rag-demo/1.0 (mailto:%s)" % MAILTO
# Browser-ish headers for PDF downloads — many publisher CDNs 403 a bare urllib UA.
BROWSER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36",
    "Accept": "application/pdf,*/*",
}

# override=True so a fresh key in .env wins over a stale one exported in the shell.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.expanduser("~/GitHub/cmbagent_lg/.env"), override=True)
except ImportError:
    pass


def _openai():
    from openai import OpenAI
    return OpenAI()  # reads OPENAI_API_KEY


def _http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _download(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers=BROWSER)
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
        if not data or b"%PDF" not in data[:1024]:
            return False
        dest.write_bytes(data)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"    download failed ({type(e).__name__}: {str(e)[:60]})", file=sys.stderr)
        return False


# ── 1. OCR (Mistral OCR) ─────────────────────────────────────────────────
# Needs the optional dependency:  pip install 'cmbagent_lg[ocr]'
from cmbagent_lg.ocr import ocr_pdf_to_dir  # noqa: E402


# ── 2–3. references via OpenAlex + open-access download ───────────────────

_FRIENDLY = ("europepmc.org", "ncbi.nlm.nih.gov", "/pmc/", "arxiv.org", "biorxiv", "medrxiv")


def _candidate_pdfs(r: dict) -> list[str]:
    """Ordered, deduped list of downloadable OA PDF URLs for one work.

    Prefers open repositories (Europe PMC, PMC, arXiv, bioRxiv) over publisher
    CDNs, which usually 403 a script.
    """
    urls = []
    for loc in (r.get("locations") or []):
        if loc.get("pdf_url"):
            urls.append(loc["pdf_url"])
    blo = (r.get("best_oa_location") or {}).get("pdf_url")
    if blo:
        urls.append(blo)
    pmcid = (r.get("ids") or {}).get("pmcid", "") or ""
    digits = "".join(ch for ch in pmcid if ch.isdigit())
    if digits:
        urls.append(f"https://www.ebi.ac.uk/europepmc/webservices/rest/PMC{digits}/fullTextPDF")
    seen, ordered = set(), []
    for u in sorted(dict.fromkeys(urls),
                    key=lambda u: 0 if any(h in u for h in _FRIENDLY) else 1):
        if u not in seen:
            seen.add(u); ordered.append(u)
    return ordered


def _europepmc_oa_pdf(doi: str | None) -> str | None:
    """A Europe PMC open-access fulltext-PDF URL for a DOI, or None."""
    if not doi:
        return None
    doi = doi.replace("https://doi.org/", "")
    q = urllib.parse.quote(f'DOI:"{doi}"')
    try:
        j = _http_json(
            f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
            f"query={q}&format=json&resultType=core&pageSize=1"
        )
        for res in j.get("resultList", {}).get("result", []):
            if res.get("pmcid") and res.get("isOpenAccess") == "Y":
                return f"https://www.ebi.ac.uk/europepmc/webservices/rest/{res['pmcid']}/fullTextPDF"
    except Exception:  # noqa: BLE001
        return None
    return None


def fetch_references(doi: str, max_refs: int) -> list[dict]:
    w = _http_json(f"https://api.openalex.org/works/doi:{doi}?mailto={MAILTO}")
    ref_ids = w.get("referenced_works", [])[:max_refs]
    out = []
    ids = "|".join(rid.rsplit("/", 1)[-1] for rid in ref_ids)
    if not ids:
        return out
    data = _http_json(
        f"https://api.openalex.org/works?filter=openalex_id:{ids}"
        f"&per-page={len(ref_ids)}&mailto={MAILTO}"
    )
    for r in data.get("results", []):
        cands = _candidate_pdfs(r)
        epmc = _europepmc_oa_pdf(r.get("doi"))
        if epmc:
            cands = [epmc] + [u for u in cands if u != epmc]
        out.append({
            "id": r["id"].rsplit("/", 1)[-1],
            "title": (r.get("title") or "")[:120],
            "year": r.get("publication_year"),
            "pdf_urls": cands,
            "is_oa": bool(r.get("open_access", {}).get("is_oa")),
        })
    return out


# ── 4. embed + sqlite-vec store ──────────────────────────────────────────

def chunk_text(text: str, source: str, size: int = 1400, overlap: int = 200) -> list[dict]:
    text = " ".join(text.split())  # collapse whitespace
    chunks, i = [], 0
    while i < len(text):
        chunks.append({"source": source, "text": text[i:i + size]})
        i += size - overlap
    return chunks


def embed_texts(texts: list[str]) -> list[list[float]]:
    client = _openai()
    out = []
    for i in range(0, len(texts), 128):  # batch
        batch = texts[i:i + 128]
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        out.extend(d.embedding for d in resp.data)
        print(f"    embedded {min(i + 128, len(texts))}/{len(texts)}", file=sys.stderr)
    return out


def _serialize(vec: list[float]) -> bytes:
    return struct.pack("%df" % len(vec), *vec)


def build_store(db_path: Path, chunks: list[dict]) -> None:
    import sqlite_vec
    embs = embed_texts([c["text"] for c in chunks])
    db_path.unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute(
        f"CREATE VIRTUAL TABLE chunks USING vec0("
        f"embedding float[{EMBED_DIM}], +source text, +text text)"
    )
    db.executemany(
        "INSERT INTO chunks(embedding, source, text) VALUES (?, ?, ?)",
        [(_serialize(e), c["source"], c["text"]) for e, c in zip(embs, chunks)],
    )
    db.commit()
    db.close()


def search(db_path: Path, query: str, topk: int) -> list[dict]:
    import sqlite_vec
    qemb = embed_texts([query])[0]
    db = sqlite3.connect(db_path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    rows = db.execute(
        "SELECT source, text, distance FROM chunks "
        "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (_serialize(qemb), topk),
    ).fetchall()
    db.close()
    return [{"source": s, "text": t, "distance": d} for s, t, d in rows]


def answer(query: str, hits: list[dict], model: str) -> str:
    ctx = "\n\n".join(f"[{i+1}] (source: {h['source']})\n{h['text']}" for i, h in enumerate(hits))
    client = _openai()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content":
                "Answer the question using ONLY the numbered context passages. "
                "Cite the passages you use as [n]. If the answer isn't in the "
                "context, say so."},
            {"role": "user", "content": f"Context:\n{ctx}\n\nQuestion: {query}"},
        ],
    )
    return resp.choices[0].message.content


# ── orchestration ─────────────────────────────────────────────────────────

def cmd_build(args) -> None:
    wd = Path(args.workdir).expanduser()
    (wd / "refs").mkdir(parents=True, exist_ok=True)
    corpus = wd / "corpus"; corpus.mkdir(exist_ok=True)
    paper_pdf = f"https://www.nature.com/articles/{args.doi.split('/')[-1]}.pdf"
    if not os.environ.get("MISTRAL_API_KEY"):
        sys.exit("MISTRAL_API_KEY not set — this demo OCRs every paper (no local fallback).")

    # 1. OCR the main paper → corpus/main_paper/{document.md, img-*.jpeg}
    print("[1/4] OCR (Mistral OCR-3) on the main paper …")
    main_pdf = wd / "main_paper.pdf"
    if not main_pdf.exists():
        _download(paper_pdf, main_pdf)
    md, nfig = ocr_pdf_to_dir(main_pdf, corpus / "main_paper", args.ocr_model)
    print(f"      → corpus/main_paper/document.md ({len(md)} chars, {nfig} figures)")

    # 2. references (OpenAlex → citation list + OA pdf candidates)
    print(f"[2/4] fetching references for {args.doi} (OpenAlex) …")
    refs = fetch_references(args.doi, args.max_refs)
    oa = [r for r in refs if r["pdf_urls"]]
    print(f"      {len(refs)} references; {len(oa)} have a candidate open-access PDF")

    # 3 + OCR. Download each OA reference and OCR it (markdown + figures).
    print(f"[3/4] downloading + OCR-ing open-access references …")
    done = total_fig = 0
    for r in oa:
        doc_dir = corpus / r["id"]
        if (doc_dir / "document.md").exists():
            done += 1; total_fig += len(list(doc_dir.glob("img-*"))); continue
        dest = wd / "refs" / f"{r['id']}.pdf"
        if not dest.exists():
            for url in r["pdf_urls"]:
                if _download(url, dest):
                    break
        if dest.exists():
            try:
                _, nf = ocr_pdf_to_dir(dest, doc_dir, args.ocr_model)
                done += 1; total_fig += nf
                print(f"      ocr {r['id']}: {nf} figures  ({r['title'][:60]})")
            except Exception as e:  # noqa: BLE001
                print(f"      ocr {r['id']} failed: {str(e)[:70]}")
    print(f"      OCR'd {done}/{len(refs)} references  ({total_fig} reference figures)")

    # 4. chunk + embed every OCR'd document → sqlite-vec
    print("[4/4] chunking + embedding (text-embedding-3-small) → sqlite-vec …")
    docs = sorted(corpus.glob("*/document.md"))
    chunks = []
    for f in docs:
        chunks += chunk_text(f.read_text(errors="ignore"), source=f.parent.name)
    print(f"      {len(chunks)} chunks from {len(docs)} OCR'd documents")
    build_store(wd / "store.db", chunks)
    print(f"\n✅ vector store ready: {wd/'store.db'}  ({len(chunks)} chunks, {len(docs)} papers)")
    print(f'   figures under {corpus}/<doc>/img-*.jpeg')
    print(f'   try:  python examples/ocr_rag_demo.py --ask "..." --workdir {args.workdir}')


def cmd_ask(args) -> None:
    wd = Path(args.workdir).expanduser()
    db = wd / "store.db"
    if not db.exists():
        sys.exit(f"No store at {db}. Run with --build first.")
    hits = search(db, args.ask, args.topk)
    print(f"\n=== top {len(hits)} retrieved chunks for: {args.ask!r} ===")
    for i, h in enumerate(hits, 1):
        print(f"\n[{i}] {h['source']}  (distance {h['distance']:.3f})")
        print("    " + " ".join(h["text"].split())[:300] + " …")
    if not args.no_answer:
        print("\n=== grounded answer ===")
        print(answer(args.ask, hits, args.chat_model))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", default="~/Desktop/ocr_rag_demo")
    ap.add_argument("--doi", default=DEFAULT_DOI)
    ap.add_argument("--build", action="store_true", help="OCR + refs + download + embed")
    ap.add_argument("--max-refs", type=int, default=45)
    ap.add_argument("--ocr-model", default="mistral-ocr-latest")
    ap.add_argument("--ask", metavar="QUESTION")
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--no-answer", action="store_true", help="retrieve only, skip the LLM answer")
    ap.add_argument("--chat-model", default="gpt-4.1")
    args = ap.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY not set (needed for embeddings + answer).")
    if args.build:
        cmd_build(args)
    if args.ask:
        cmd_ask(args)
    if not (args.build or args.ask):
        ap.print_help()


if __name__ == "__main__":
    main()
