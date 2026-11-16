"""Discover generated plots and attach them to an agent prompt as image blocks."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import List, Union

# Raster/vector formats the vision models accept. SVG/PDF are excluded — most
# chat models can't ingest them as images.
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# Providers cap individual images (Anthropic/OpenAI ~5 MB); stay well under, and
# bound the count so a plot-heavy step doesn't blow up the prompt / cost.
_MAX_BYTES = 4_000_000
_MAX_IMAGES = 8

_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def collect_images(work_dir, max_images: int = _MAX_IMAGES) -> List[Path]:
    """Plot images under ``{work_dir}/data`` (cmbagent_lg's output dir), newest
    first, capped at ``max_images``. Skips ``*_failure*`` artifacts.

    Returns ``[]`` when there's no work_dir or data dir.
    """
    if not work_dir:
        return []
    data = Path(work_dir).expanduser() / "data"
    if not data.is_dir():
        return []
    imgs = [
        p for p in data.iterdir()
        if p.is_file()
        and p.suffix.lower() in _IMG_EXTS
        and not (p.stem.endswith("_failure") or "_failure_" in p.stem)
    ]
    imgs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return imgs[:max_images]


def images_from_manifest(work_dir, data_manifest, max_images: int = _MAX_IMAGES) -> List[Path]:
    """Image files THIS step produced, taken from its `data_manifest`.

    Scoped to the current step (unlike `collect_images`, which scans all of
    `data/`) — so the image reviewer only judges the plots the running step
    actually made. Skips `*_failure*` artifacts and missing files.
    """
    if not work_dir or not data_manifest:
        return []
    wd = Path(work_dir).expanduser()
    out: List[Path] = []
    for entry in data_manifest:
        rel = (entry or {}).get("path", "")
        p = wd / rel
        if (p.suffix.lower() in _IMG_EXTS
                and not (p.stem.endswith("_failure") or "_failure_" in p.stem)
                and p.is_file()):
            out.append(p)
    return out[:max_images]


def _data_url(path: Path) -> str:
    mime = _MIME.get(path.suffix.lower(), "image/png")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def with_images(
    text: str, paths: List[Path], max_bytes: int = _MAX_BYTES
) -> Union[str, list]:
    """Build a LangChain message ``content`` combining ``text`` with each image.

    Returns a multimodal content list (text block, then a labelled text+image
    block per usable figure) when there are images to attach; otherwise returns
    ``text`` unchanged, so callers can pass the result straight to
    ``HumanMessage(content=...)`` either way. Images over ``max_bytes`` or that
    fail to read are skipped (logged to stderr).
    """
    content: list = [{"type": "text", "text": text}]
    attached = 0
    for p in paths:
        try:
            if p.stat().st_size > max_bytes:
                print(f"[vlm] skipping oversized image {p.name} "
                      f"({p.stat().st_size} bytes)", file=sys.stderr)
                continue
            url = _data_url(p)
        except OSError as e:  # noqa: BLE001 — a bad image must never break a run
            print(f"[vlm] could not read image {p}: {e}", file=sys.stderr)
            continue
        content.append({"type": "text", "text": f"\nFigure — data/{p.name}:"})
        content.append({"type": "image_url", "image_url": {"url": url}})
        attached += 1
    return content if attached else text
