#!/usr/bin/env python
"""Score a candidate cmbagent_lg's deterministic claim grounding on the labelled corpus.

    bench_claims.py --pkg /path/to/checkout [--claims claims.json] [--relabel] [--json out.json]

The corpus (`claims.json`, built by `build_claims.py`) is every claim the LLM claim
auditor ever called `unsupported_hard` on a fleet, labelled by scanning the iteration's
FULL sources: `false_hard` = every number in the claim exists in the sources (a false
accusation), `true_hard` = none does (a real fabrication), `mixed`, `no_numbers`.

For each claim the candidate's `SourceIndex` is rebuilt from the same source set the
builder used (re-read from the iteration on disk), the claim is grounded, and the
candidate's verdict policy (`apply_overrides`) is applied to the REAL audit record, so
"rescued" means what the pipeline would do: the hard verdict demoted without an LLM.

Gates: false_hard rescued ≥ 30/32 (or, after `--relabel` adjudication, every whole-token
false_hard); true_hard rescued 0/12; mixed → at most weak; adversarial corpus (generated
here from the same indexes: excluded-tier tokens absent from primary, values perturbed
past tolerance) → 0 accepted. Timing: index < 2 s, ledger < 0.2 s.

A checkout without `cmbagent_lg.researcher.grounding` (main before this change) scores
as the status quo: nothing is rescued.
"""
import argparse
import collections
import importlib
import json
import os
import re
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--pkg", required=True)
ap.add_argument("--claims", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "claims.json"))
ap.add_argument("--relabel", action="store_true", help="print disagreements with source lines")
ap.add_argument("--json")
ap.add_argument("--no-adversarial", action="store_true")
a = ap.parse_args()

sys.path.insert(0, os.path.abspath(a.pkg))
for m in [m for m in list(sys.modules) if m.startswith("cmbagent_lg")]:
    del sys.modules[m]
import cmbagent_lg  # noqa: E402
assert os.path.abspath(a.pkg) in os.path.abspath(cmbagent_lg.__file__), \
    f"loaded {cmbagent_lg.__file__}, not the candidate"
rows = json.load(open(a.claims))
print(f"candidate: {cmbagent_lg.__file__}")
print(f"corpus: {len(rows)} claims, labels {dict(collections.Counter(r['label'] for r in rows))}")

try:
    G = importlib.import_module("cmbagent_lg.researcher.grounding")
except ImportError:
    G = None
if G is None:
    print("\nno cmbagent_lg.researcher.grounding in this checkout → status quo: 0 rescued")
    by = collections.Counter(r["label"] for r in rows)
    print(f"false_hard rescued 0/{by['false_hard']}   true_hard rescued 0/{by['true_hard']}   "
          f"mixed 0/{by['mixed']}   no_numbers 0/{by['no_numbers']}")
    print("GATES: false_hard ≥ 30/32: FAIL · true_hard 0/12: PASS · adversarial: n/a")
    sys.exit(0)

from cmbagent_lg.self_debug.schemas import ClaimAudit, ClaimCheck  # noqa: E402

NUM_SUB = re.compile(r"(?<![\w.^])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.]?\d)")


def itdir_of(row):
    return row["audit"].split("/experiment_output")[0]


def real_claim(row):
    """The full ClaimCheck from the audit file on disk (the corpus row truncates evidence)."""
    try:
        d = json.load(open(row["audit"]))
        for c in d.get("claims", []):
            if c.get("verdict") == "unsupported_hard" and c.get("statement", "")[:300] == row["statement"]:
                return ClaimCheck(**{k: c.get(k) for k in ("statement", "kind", "verdict", "evidence")})
    except Exception:  # noqa: BLE001
        pass
    return ClaimCheck(statement=row["statement"], kind="quantitative", verdict="unsupported_hard",
                      evidence=row.get("evidence") or "")


def old_window_visible(row, wd, step):
    """Would the OLD graders have seen every number? They saw only the `output` excerpts
    of the researcher's tool log (4,000 chars of each read)."""
    try:
        nums = [n.text for n in G.extract_claim_numbers(row["statement"]) if n.kind != "bound"]
        if not nums:
            return None
        recs = G._read_records(wd, step)
        blob = "\n".join((r.get("output") or "") for r in recs.values())
        return all(re.search(r"(?<![\w.])" + re.escape(n.replace(",", "")) + r"(?![\w.]?\d)", blob.replace(",", ""))
                   for n in nums)
    except Exception:  # noqa: BLE001
        return None


indexes = {}
index_times = []
results = []
t_ground = 0.0
for row in rows:
    it = itdir_of(row)
    wd = os.path.join(it, "experiment_output")
    step = row.get("step") or 1
    key = (it, step)
    if key not in indexes:
        t = time.perf_counter()
        indexes[key] = G.SourceIndex.build(wd, step)
        dt = time.perf_counter() - t
        index_times.append((dt, indexes[key].coverage.indexed_bytes, it.split("/")[-2] + "/" + it.split("/")[-1]))
    idx = indexes[key]
    t = time.perf_counter()
    g = G.ground(row["statement"], idx)
    claim = real_claim(row)
    audit = ClaimAudit(claims=[claim])
    notes = G.apply_overrides(audit, {claim.statement: g}, idx.coverage, index=idx)
    t_ground += time.perf_counter() - t
    rescued = claim.verdict != "unsupported_hard"
    grounded = g.rescuable and not g.wrong_citation
    results.append({"i": rows.index(row), "label": row["label"], "state": g.state, "rescued": rescued,
                    "grounded": grounded,
                    "verdict": claim.verdict, "action": notes[0][1] if notes else "",
                    "cited_ok": g.cited_and_verified, "wrong_citation": g.wrong_citation,
                    "old_visible": old_window_visible(row, wd, step), "it": key[0].split("/")[-2:],
                    "statement": row["statement"][:120], "grounding": g, "index": idx})

# ── report ──────────────────────────────────────────────────────────────
print("\nstate × label:")
table = collections.defaultdict(collections.Counter)
for r in results:
    table[r["label"]][r["state"]] += 1
for label in ("false_hard", "true_hard", "mixed", "no_numbers"):
    print(f"  {label:11s} " + ", ".join(f"{s}={n}" for s, n in sorted(table[label].items())))

by = collections.Counter(r["label"] for r in results)
resc = collections.Counter(r["label"] for r in results if r["grounded"])
policy = collections.Counter(r["label"] for r in results if r["rescued"])
print("\nrescued per label — grounding level (numbers found in this run's own outputs: the gate) "
      "/ policy level (hard demoted after the auditor's receipts are honoured):")
for label in ("false_hard", "true_hard", "mixed", "no_numbers"):
    print(f"  {label:11s} grounding {resc[label]}/{by[label]}   policy {policy[label]}/{by[label]}")

print("\nnumbers visible in the OLD grader windows (researcher tool-log excerpts) per label:")
for label in ("false_hard", "true_hard", "mixed"):
    vis = [r["old_visible"] for r in results if r["label"] == label and r["old_visible"] is not None]
    print(f"  {label:11s} {sum(1 for v in vis if v)}/{len(vis)} fully visible")

print("\nper claim:")
for r in results:
    flag = "" if (r["grounded"] == (r["label"] == "false_hard")) else "  <-- disagrees with label"
    print(f"  [{r['i']:2d}] {r['label']:10s} {r['state']:15s} {'FOUND  ' if r['grounded'] else 'absent '} "
          f"{'RESCUED' if r['rescued'] else 'stands ':7s} "
          f"{r['action']:28s} {'/'.join(r['it'])} | {r['statement'][:80]}{flag}")

print("\ntiming:")
index_times.sort(reverse=True)
print(f"  index: {len(index_times)} builds, max {index_times[0][0]:.3f} s ({index_times[0][1]/1e6:.1f} MB, "
      f"{index_times[0][2]}), mean {sum(t for t, _, _ in index_times)/len(index_times):.3f} s")
print(f"  grounding + policy: {t_ground:.3f} s total for {len(results)} claims "
      f"({1000*t_ground/len(results):.1f} ms/claim)")
t = time.perf_counter()
some = results[0]["index"]
big = max(results, key=lambda r: r["index"].n_tokens)["index"]
qs = G.ground_report("\n".join(r["statement"] for r in results), big)
ledger = G.render_ledger(qs, big.coverage)
t_ledger = time.perf_counter() - t
print(f"  ledger: {len(qs)} numbers against the largest index ({big.n_tokens} tokens) in {t_ledger:.3f} s")

if a.relabel:
    print("\nrelabel candidates:")
    for r in results:
        if r["label"] == "false_hard" and not r["grounded"]:
            print(f"  [{r['i']}] false_hard NOT rescued — {r['state']}; builder hits: "
                  f"{[(h[0], h[1], h[2]) for h in rows[r['i']]['hits']]}")
            for q in r["grounding"].quantities:
                print("      " + G.render_quantity(q, r["index"].coverage).replace("\n", "\n      ")[:400])
            # substring evidence: where does the builder's verbatim `tok in text` hit live?
            it = itdir_of(rows[r["i"]])
            for tok, fname, how in rows[r["i"]]["hits"]:
                if not fname:
                    continue
                sub, name = fname.split(":", 1)
                path = os.path.join(it, "experiment_output", "codebase" if sub == "log" else "data", name)
                try:
                    for ln, line in enumerate(open(path, errors="replace"), 1):
                        if tok in line:
                            whole = bool(re.search(r"(?<![\w.])" + re.escape(tok) + r"(?![\w.]?\d)", line))
                            print(f"      builder hit {tok} in {fname}:{ln} {'WHOLE-TOKEN' if whole else 'SUBSTRING'}: {line.strip()[:120]!r}")
                            break
                except OSError:
                    pass
        if r["label"] == "true_hard" and r["grounded"]:
            print(f"  [{r['i']}] true_hard RESCUED — {r['state']}")
            for q in r["grounding"].quantities:
                print("      " + G.render_quantity(q, r["index"].coverage).replace("\n", "\n      ")[:400])
        if r["label"] == "mixed":
            print(f"  [{r['i']}] mixed → {r['state']} / {r['verdict']}")

# ── adversarial corpus ──────────────────────────────────────────────────
adv_accepted = []
adv_n = 0
if not a.no_adversarial:
    print("\nadversarial corpus (excluded-tier tokens absent from primary; primary values perturbed past tolerance):")
    for key, idx in indexes.items():
        excluded = []
        primary = []
        for e, toks in idx.tokens_by_exp.items():
            for t in toks:
                if t.tier in ("primary", "figure"):
                    if not t.meta and t.decimals >= 3 and t.sig >= 3:
                        primary.append(t)
                elif t.tier in ("input", "superseded", "meta"):
                    if t.sig >= 3:
                        excluded.append(t)

        def in_primary(value, decimals, pct=False):
            """Absent from primary means no primary-tier token within the claim's own
            rounding tolerance (meta included: an integer may match a shape/count).
            A percent token is also present when its FRACTION form is (and vice
            versa) — the labelled percent rule the grounding applies; without this
            the generator manufactures a claim whose value the run did compute."""
            for tid in idx.near(value, 0.5 * 10.0 ** (-decimals) * (1 + 1e-9)):
                if idx.token(tid).tier in ("primary", "figure"):
                    return True
            alt, d2 = (value / 100.0, decimals + 2) if pct else (value * 100.0, decimals - 2)
            for tid in idx.near(alt, 0.5 * 10.0 ** (-d2) * (1 + 1e-9)):
                if idx.token(tid).tier in ("primary", "figure"):
                    return True
            return False
        claims = []
        for t in excluded:
            if in_primary(t.value, t.decimals, t.pct):
                continue
            w = next((w for w in G._words(t.label) if len(w) >= 3 and w not in G._STOPWORDS), "x")
            claims.append((f"{w} = {t.text}", "excluded:" + t.tier))
            if len(claims) >= 12:
                break
        n_pert = 0
        for t in primary[::max(1, len(primary) // 12)]:
            pert = t.value + 3 * 10.0 ** (-t.decimals) * (1 if t.value >= 0 else -1)
            txt = f"{pert:.{t.decimals}f}"
            if in_primary(float(txt), t.decimals):
                continue
            w = next((w for w in G._words(t.label) if len(w) >= 3 and w not in G._STOPWORDS), "x")
            claims.append((f"{w} = {txt}", f"perturbed from {t.text}"))
            n_pert += 1
            if n_pert >= 12:
                break
        for s, why in claims:
            adv_n += 1
            g = G.ground(s, idx)
            if g.state in ("grounded", "weakly_grounded"):
                adv_accepted.append((key[0].split("/")[-2:], s, why, g.state,
                                     [G.render_quantity(q, idx.coverage)[:200] for q in g.quantities]))
    print(f"  {adv_n} adversarial claims over {len(indexes)} indexes → {len(adv_accepted)} accepted "
          f"(grounded/weakly_grounded)")
    kinds = collections.Counter((why.split(" ")[0], st) for _, _, why, st, _ in adv_accepted)
    print(f"  accepted by (origin, state): {dict(kinds)}")
    for it, s, why, st, ren in adv_accepted[:40]:
        print(f"    ACCEPTED {st}: {s!r} ({why}) in {'/'.join(it)}")
        for x in ren:
            print("       " + x)

# ── gates ───────────────────────────────────────────────────────────────
fh, th = resc["false_hard"], resc["true_hard"]  # grounding level
mixed_ok = all(r["verdict"] != "unsupported_hard" or r["state"] not in ("grounded", "weakly_grounded")
               for r in results if r["label"] == "mixed")
mixed_states = [r["state"] for r in results if r["label"] == "mixed"]
print("\nGATES:")
print(f"  false_hard rescued {fh}/{by['false_hard']}: {'PASS' if fh >= 30 else 'FAIL (or adjudicate with --relabel)'}")
print(f"  true_hard rescued {th}/{by['true_hard']}: {'PASS' if th == 0 else 'FAIL (re-adjudicate percent-form matches)'}")
print(f"  mixed states {mixed_states}: {'PASS (never grounded)' if 'grounded' not in mixed_states else 'CHECK'}")
if not a.no_adversarial:
    print(f"  adversarial accepted {len(adv_accepted)}/{adv_n}: {'PASS' if not adv_accepted else 'FAIL'}")
print(f"  index max {index_times[0][0]:.3f} s (< 2 s): {'PASS' if index_times[0][0] < 2 else 'FAIL'}; "
      f"ledger {t_ledger:.3f} s (< 0.2 s): {'PASS' if t_ledger < 0.2 else 'FAIL'}")

if a.json:
    json.dump([{k: v for k, v in r.items() if k not in ("grounding", "index")} for r in results],
              open(a.json, "w"), indent=1)
