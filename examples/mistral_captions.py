"""Structure Mistral OCR captions — pair each figure with the text block that
follows its image reference in `document.md`.

Mistral transcribes captions inline but emits no structured figure->caption
link, so this walks the markdown blocks, attaches the following text block to
each image, and flags whether it looks like a real caption.

    python examples/mistral_captions.py --dir ~/Desktop/ocr_test/2604.25385/mistral

Writes `<dir>/captions.json` and `<dir>/captions.md`, prints a summary.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

IMG = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")            # ![alt](path) -> path
CAPTION_START = re.compile(r"(?i)^\s*(figure|fig\.?|table|tab\.?)\s*\d")


def structure_captions(md: str) -> list[dict]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", md)]
    out = []
    for i, b in enumerate(blocks):
        m = IMG.search(b)
        if not m:
            continue
        img = m.group(1)
        # caption candidate: text after the ref in the same block, else next text block
        after = b[m.end():].strip()
        cap = after or None
        if cap is None:
            for nb in blocks[i + 1:]:
                if not nb:
                    continue
                if IMG.search(nb):      # next thing is another image -> no adjacent caption
                    break
                cap = nb
                break
        cap = re.sub(r"\s+", " ", cap).strip() if cap else ""
        out.append({
            "figure": img,
            "caption": cap,
            "is_caption": bool(CAPTION_START.match(cap)),
        })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="Mistral output dir containing document.md")
    a = ap.parse_args()
    d = Path(a.dir).expanduser()
    caps = structure_captions((d / "document.md").read_text())

    (d / "captions.json").write_text(json.dumps(caps, indent=1))
    lines = ["| figure | caption? | caption |", "|---|---|---|"]
    for c in caps:
        mark = "✅" if c["is_caption"] else "—"
        lines.append(f"| `{c['figure']}` | {mark} | {c['caption'][:100] or '(none adjacent)'} |")
    (d / "captions.md").write_text("\n".join(lines) + "\n")

    linked = sum(c["is_caption"] for c in caps)
    print(f"{d.name}: {len(caps)} figures | {linked} with a real caption "
          f"({linked}/{len(caps)})  -> captions.json / captions.md")
    for c in caps:
        mark = "✅" if c["is_caption"] else "· "
        print(f"  {mark} {c['figure']:<12} {c['caption'][:90] or '(none adjacent)'}")


if __name__ == "__main__":
    main()
