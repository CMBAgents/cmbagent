"""Shared prompt-loading helpers used by `planning` and `self_debug`.

Both modules' `prompts.py` render YAML templates against a `PlanContext`-derived
dict with `str.format_map(SafeDict(...))`.
"""

from importlib import resources
import yaml


class SafeDict(dict):
    """`format_map` helper: missing keys render as empty strings instead of KeyError."""

    def __missing__(self, key):
        return ""


def load_yaml(package: str, name: str) -> dict:
    """Load `<package>/<name>` as a parsed YAML dict via `importlib.resources`.

    Pass the dotted package path (e.g. `"cmbagent_lg.planning.templates"`)
    and the YAML filename (e.g. `"planner.yaml"`).
    """
    text = resources.files(package).joinpath(name).read_text()
    return yaml.safe_load(text)


def schema_field_brief(schema) -> str:
    """One bullet per Pydantic field — drop into a generator prompt so the
    LLM knows which fields to cover before the formatter extracts them."""
    lines = []
    for name, field in schema.model_fields.items():
        t = str(field.annotation).replace("typing.", "")
        if t.startswith("<class '") and t.endswith("'>"):
            t = t[len("<class '") : -len("'>")]
        lines.append(f"- {name} ({t}): {field.description or ''}")
    return "\n".join(lines)


def flatten_content(c) -> str:
    """Coerce a LangChain message `.content` to a plain string.

    Gemini (and others) return content as either a string or a list of
    part-dicts, e.g. `[{"type": "text", "text": "..."}, ...]`.
    """
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return str(c)
