"""Command-line entry points.

Registered as console scripts in pyproject.toml. After `pip install -e .`:

    cmbagent-lg-cost                              # latest trace in project
    cmbagent-lg-cost <trace_id>                   # specific trace
    cmbagent-lg-cost <work_dir>                   # reads langfuse_trace_id.txt
    cmbagent-lg-cost <path/to/langfuse_trace_id.txt>

Also runnable as a module:

    python -m cmbagent_lg.cli cost [arg]
"""

import sys
import os
import base64
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

from cmbagent_lg.tracing import langfuse_host


def _auth_header() -> str:
    # override=False: a dotenv is a convenience for an unconfigured shell, never
    # an override for a configured one (it would hijack a deployed LANGFUSE_*).
    load_dotenv(override=False)
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    if not pk or not sk:
        sys.exit(
            "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set — put the keys for the project "
            "holding the trace in .env. See README.md § Langfuse."
        )
    return base64.b64encode(f"{pk}:{sk}".encode()).decode()


def _get(path: str) -> dict:
    auth = _auth_header()  # runs load_dotenv(), so it must precede langfuse_host()
    req = urllib.request.Request(
        f"{langfuse_host()}{path}", headers={"Authorization": f"Basic {auth}"}
    )
    return json.loads(urllib.request.urlopen(req).read())


# Langfuse v4 replaced `GET /api/public/observations` with
# `GET /api/public/v2/observations`: time-windowed (fromStartTime/toStartTime),
# cursor-paginated, and trimmed unless cost/usage/model/latency are requested
# through `fields`.
_OBS_FIELDS = "core,basic,usage,model,metrics,metadata"
_OBS_PAGE_LIMIT = 1000  # v2 maximum
_LOOKBACK_DAYS = int(os.environ.get("CMBAGENT_LG_COST_LOOKBACK_DAYS", "30"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _observations_in_window(
    trace_id: str | None, days: int, limit: int = _OBS_PAGE_LIMIT, max_pages: int = 1000
) -> list[dict]:
    """All observations in the last `days`, optionally for one trace."""
    now = datetime.now(timezone.utc)
    params = {
        "fromStartTime": _iso(now - timedelta(days=days)),
        # a little slack: clock skew between this box and the ingester
        "toStartTime": _iso(now + timedelta(minutes=5)),
        "limit": limit,
        "fields": _OBS_FIELDS,
        # metadata values >200 chars are truncated by default; the agent tag
        # lives in metadata["tags"], so ask for it in full.
        "expandMetadata": "tags",
    }
    if trace_id:
        params["traceId"] = trace_id

    out: list[dict] = []
    for _ in range(max_pages):
        resp = _get("/api/public/v2/observations?" + urllib.parse.urlencode(params))
        batch = resp.get("data") or []
        out.extend(batch)
        cursor = (resp.get("meta") or {}).get("cursor")
        if not batch or not cursor:
            break
        params["cursor"] = cursor
    return out


def _all_observations(trace_id: str) -> list[dict]:
    obs = _observations_in_window(trace_id, _LOOKBACK_DAYS)
    if not obs:
        # The window is only there because v4 demands one — widen it rather
        # than report an empty trace that is merely old.
        obs = _observations_in_window(trace_id, 3650)
    return obs


def _resolve_trace_id(arg: str) -> str:
    """Accept a trace ID, a workdir path, or a langfuse_trace_id.txt path."""
    p = Path(arg)
    if p.is_dir():
        p = p / "langfuse_trace_id.txt"
    if p.is_file():
        return p.read_text().strip()
    return arg  # not a path — treat as raw trace id


_KNOWN_AGENT_TAGS = (
    # planning module
    "planner",
    "plan_reviewer",
    "format_plan",
    "format_review",
    # self_debug module (executor makes no LLM call → never a GENERATION)
    "intent_gate",
    "engineer",
    "format_engineer",
    "execution_evaluator",
    "step_evaluator",
    "data_fixer",
    "escalation",
    # researcher module
    "researcher",
    "react_researcher",
    "researcher_step_evaluator",
    "researcher_view_image",
    # vlm
    "image_reviewer",
    # deep_research (outer orchestrator)
    "run_step",
    "adaptive_review",
    "format_adaptive_review",
    "replan_tail",
    "format_tail",
    # teaching demos
    "react_demo",
    "eval_demo",
)


def _latency_s(o: dict) -> float | None:
    """Generation latency in seconds from the ISO-8601 `startTime`/`endTime`, or
    None if either is missing or unparseable."""
    s, e = o.get("startTime"), o.get("endTime")
    if not s or not e:
        return None
    try:
        # `Z` suffix → +00:00 for fromisoformat
        ts0 = datetime.fromisoformat(s.replace("Z", "+00:00"))
        ts1 = datetime.fromisoformat(e.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (ts1 - ts0).total_seconds()


def trace_summary(trace_id: str) -> None:
    obs = _all_observations(trace_id)
    if not obs:
        sys.exit(
            f"no observations found for trace {trace_id} — is it flushed to "
            f"{langfuse_host()} yet?"
        )

    by_model: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "in_tok": 0, "out_tok": 0, "cost": 0.0}
    )
    by_tag: dict[str, dict] = defaultdict(
        lambda: {
            "calls": 0,
            "in_tok": 0,
            "out_tok": 0,
            "cost": 0.0,
            "models": set(),
            "latencies_s": [],
        }
    )

    for o in obs:
        if o.get("type") != "GENERATION":
            continue
        # v2 exposes cost as `totalCost` / `costDetails.total`;
        # `calculatedTotalCost` is the v3 name, kept for older servers.
        costs = o.get("costDetails") or {}
        cost = float(
            o.get("totalCost")
            or costs.get("total")
            or o.get("calculatedTotalCost")
            or 0
        )
        usage = o.get("usageDetails") or {}
        in_tok = o.get("inputUsage") or usage.get("input") or o.get("promptTokens") or 0
        out_tok = (
            o.get("outputUsage") or usage.get("output") or o.get("completionTokens") or 0
        )
        model = o.get("model") or o.get("providedModelName") or "?"
        # v2 gives latency directly (seconds); fall back to endTime - startTime.
        lat = o.get("latency")
        if lat is None:
            lat = _latency_s(o)

        by_model[model]["calls"] += 1
        by_model[model]["in_tok"] += in_tok
        by_model[model]["out_tok"] += out_tok
        by_model[model]["cost"] += cost

        for t in (
            (o.get("metadata") or {}).get("tags") or o.get("tags") or []
        ):
            if t in _KNOWN_AGENT_TAGS:
                row = by_tag[t]
                row["calls"] += 1
                row["in_tok"] += in_tok
                row["out_tok"] += out_tok
                row["cost"] += cost
                row["models"].add(model)
                if lat is not None:
                    row["latencies_s"].append(lat)

    print(f"trace {trace_id}\n")

    # --- by model (unchanged) ---
    print("--- by model ---")
    print(f"{'key':40s} {'calls':>6s} {'in_tok':>8s} {'out_tok':>8s} {'$cost':>10s}")
    total = 0.0
    for k, d in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]):
        print(
            f"{k:40s} {d['calls']:>6d} {d['in_tok']:>8d} "
            f"{d['out_tok']:>8d} {d['cost']:>10.6f}"
        )
        total += d["cost"]
    print(f"{'TOTAL':40s} {'':>6s} {'':>8s} {'':>8s} {total:>10.6f}\n")

    # --- by agent (enriched: model, calls, tokens, latency, cost) ---
    if not by_tag:
        return
    print("--- by agent (tag) ---")
    hdr = (
        f"{'agent':22s} {'model':30s} {'calls':>5s} {'in_tok':>7s} "
        f"{'out_tok':>7s} {'avg_s':>6s} {'min_s':>6s} {'max_s':>6s} {'$cost':>10s}"
    )
    print(hdr)
    total = 0.0
    for k, d in sorted(by_tag.items(), key=lambda kv: -kv[1]["cost"]):
        models = ", ".join(sorted(d["models"])) or "?"
        lats = d["latencies_s"]
        if lats:
            avg_s = f"{sum(lats) / len(lats):.2f}"
            min_s = f"{min(lats):.2f}"
            max_s = f"{max(lats):.2f}"
        else:
            avg_s = min_s = max_s = "-"
        print(
            f"{k:22s} {models:30s} {d['calls']:>5d} {d['in_tok']:>7d} "
            f"{d['out_tok']:>7d} {avg_s:>6s} {min_s:>6s} {max_s:>6s} "
            f"{d['cost']:>10.6f}"
        )
        total += d["cost"]
    print(
        f"{'TOTAL':22s} {'':30s} {'':>5s} {'':>7s} {'':>7s} "
        f"{'':>6s} {'':>6s} {'':>6s} {total:>10.6f}\n"
    )


def cost_main() -> None:
    """`cmbagent-lg-cost` entry point."""
    if len(sys.argv) > 1:
        trace_id = _resolve_trace_id(sys.argv[1])
    else:
        # v4 has no trace-list endpoint here: the newest observation names the
        # newest trace (v2 returns them newest-first).
        latest = _observations_in_window(None, _LOOKBACK_DAYS, limit=1, max_pages=1)
        if not latest:
            sys.exit(
                f"no observations in the last {_LOOKBACK_DAYS} days — pass a trace id "
                "or work_dir, or raise CMBAGENT_LG_COST_LOOKBACK_DAYS."
            )
        trace_id = latest[0]["traceId"]
    trace_summary(trace_id)


def _module_main() -> None:
    """`python -m cmbagent_lg.cli <subcommand> [args]`."""
    sub = sys.argv[1] if len(sys.argv) > 1 else None
    if sub == "cost":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        cost_main()
    else:
        sys.exit(f"usage: python -m cmbagent_lg.cli cost [trace_id|work_dir]")


if __name__ == "__main__":
    _module_main()
