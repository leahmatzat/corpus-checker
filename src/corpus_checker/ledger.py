"""The ledger: registry/*.yaml inverted on dataset. DERIVED — never authored, never committed.

It answers the question no per-model check can: the same dataset is training data for
one model and evaluation data for another. That cross-model view is what someone
comparing models on a shared benchmark needs.

`build_ledger()` returns a plain JSON-able dict. Its shape is the contract the static
site renders (LEDGER_VERSION bumps on any breaking change):

    {
      "ledger_version": 3,
      "models":   {model_id: {model, version, model_class, paper, draft, reviewed_by,
                              reviewed_date, review_checked, disputed, evaluated_on: [{dataset, task,
                              obtained_from}], corpora: [{stage, name, manifest_type,
                              identifier_type, quote, pointer, sources, sources_searched,
                              caveats}]}},
      "datasets": {dataset_id: {name, kind, organism, identifiers, sources, note,
                                evaluated_by: [model_id], exposure: [row], reuse: [...]}},
      "rows":     [{dataset, model, stage, relation, verdict, keys_attempted, keys_absent, matched_on,
                    overlap_level, samples, sample_ids, cells, reason, note}]
    }

There is NO model-level score anywhere in it, by design.
"""
from __future__ import annotations

from pathlib import Path

from .registry import load_catalog, load_yaml, registry_paths, relation, review_of

LEDGER_VERSION = 3
_ROW_FIELDS = ("keys_attempted", "keys_absent", "matched_on", "match_level", "identity_note", "identity", "identity_basis", "by_design", "overlap_level", "samples",
               "sample_ids", "cells", "reason", "note")


def build_ledger(root: Path) -> dict:
    catalog = load_catalog(root)
    models, rows = {}, []

    for path in registry_paths(root):
        e = load_yaml(path)
        reviewer, review_date, review_checked = review_of(e)
        models[e["id"]] = {
            "model": e["model"],
            "version": e.get("version"),
            "model_class": e["model_class"],
            "paper": e["paper"],
            "draft": not reviewer,
            "reviewed_by": reviewer or None,
            "reviewed_date": review_date,
            "review_checked": list(review_checked),
            "disputed": e.get("disputed"),
            "discovery_unchecked": sorted(k for k, item in e["discovery"].items() if not item["checked"]),
            "evaluated_on": e["evaluated_on"],
            "corpora": [{
                "stage": c["stage"],
                "name": c.get("name"),
                "manifest_type": c["manifest"]["type"],
                "identifier_type": c["manifest"].get("identifier_type"),
                "quote": c["manifest"]["quote"],
                "pointer": c["manifest"].get("pointer"),
                "sources": [{**{k: s.get(k) for k in ("name", "url", "sha256", "acquired", "confirmation", "records")},
                             "committed": bool(s.get("snapshot") or s.get("extract"))}
                            for s in c["manifest"].get("sources", [])],
                "sources_searched": c["result"]["sources_searched"],
                "unresolved_entries": c["manifest"].get("unresolved_entries", []),
                "splits": c["manifest"].get("splits", []),
                "totals": c["manifest"].get("totals"),
                "joined_through": [tb["name"].split(" — ")[0] for s in c["manifest"].get("sources", [])
                                   for tb in (s.get("join") or {}).get("tables", [])],
                "caveats": c["result"].get("caveats", []),
            } for c in e["corpora"]],
        }
        for c in e["corpora"]:
            for f in c["result"]["findings"]:
                row = {"dataset": f["dataset"], "model": e["id"], "stage": c["stage"],
                       "relation": relation(e, f["dataset"]), "verdict": f["verdict"]}
                row.update({k: f[k] for k in _ROW_FIELDS if k in f})
                rows.append(row)

    datasets = {}
    for d_id, d in sorted(catalog.items()):
        d_rows = [r for r in rows if r["dataset"] == d_id]
        evaluated_by = sorted(m_id for m_id, m in models.items() if any(x["dataset"] == d_id for x in m["evaluated_on"]))
        # A by-design finding is a model trained on its own evaluation dataset on purpose: not exposure.
        exposure = [r for r in d_rows if r["verdict"] == "PRESENT" and not r.get("by_design")]
        datasets[d_id] = {
            **{k: d.get(k) for k in ("name", "title", "kind", "organism", "identifiers", "subsets", "sources", "note")},
            "evaluated_by": evaluated_by,
            "exposure": exposure,
            # ★ The reuse map: this dataset is in model A's training data AND model B evaluates on it.
            "reuse": [{"in_training_of": x["model"], "stage": x["stage"], "evaluated_by": b}
                      for x in exposure for b in evaluated_by if b != x["model"]],
        }

    return {"ledger_version": LEDGER_VERSION, "models": models, "datasets": datasets,
            "rows": sorted(rows, key=lambda r: (r["dataset"], r["model"], r["stage"]))}


def judgment_calls(ledger: dict, model_id: str) -> list[str]:
    """The calls in a model's entry that rest on a person's judgment, in plain words.

    Shown to the reviewer by `corpus-checker review` and listed on the model page under the
    review — the same list in both places.
    """
    datasets, model = ledger["datasets"], ledger["models"][model_id]
    calls: list[str] = []
    for r in ledger["rows"]:
        if r["model"] != model_id:
            continue
        name = (datasets.get(r["dataset"]) or {}).get("name") or r["dataset"]
        if r.get("identity") == "provisional":
            calls.append(f"{name}: provisional identity ({r['stage']})")
        if r.get("keys_absent"):
            keys = ", ".join(k.upper() if k in ("pmid", "doi") else k for k in r["keys_absent"])
            calls.append(f"{name}: no {keys} exists, recorded with evidence ({r['stage']})")
        if r.get("identity_note"):
            calls.append(f"{name}: a paper-level match accepted as this dataset ({r['stage']})")
        if r.get("by_design"):
            calls.append(f"{name}: trained on by design, holding out {r['by_design']} ({r['stage']})")
    n_caveats = sum(len(c.get("caveats") or []) for c in model["corpora"])
    if n_caveats:
        calls.append(f"the wording of {n_caveats} caveat{'s' if n_caveats != 1 else ''}")
    return calls


def review_points(ledger: dict, model_id: str) -> dict[str, list[str]]:
    """What each of the five review checks covers for THIS entry, in plain words.

    The review command shows these under each question, and the model page under each check,
    so what the reviewer saw and what readers see are the same lines. Derived from the entry;
    never written by hand.
    """
    datasets, model = ledger["datasets"], ledger["models"][model_id]
    name = lambda did: (datasets.get(did) or {}).get("name") or did

    paper, when = model["paper"], ", ".join(x for x in (model["paper"].get("journal"), model["paper"].get("published")) if x)
    ids = [f"DOI {paper['doi']}" if paper.get("doi") else "", f"PMID {paper['pmid']}" if paper.get("pmid") else "",
           f"OpenReview {paper['openreview']}" if paper.get("openreview") else "",
           "its proceedings page" if paper.get("url") else ""]
    paper_points = [f"{when}: {' · '.join(i for i in ids if i)}" if when else " · ".join(i for i in ids if i)]
    if absent := [a["key"].upper() for a in paper.get("absent", [])]:
        paper_points.append(f"No {' or '.join(absent)} exists for it (recorded with the evidence).")
    if paper.get("preprint"):
        paper_points.append(f"Preprint {paper['preprint']}, not the version scored.")

    manifest_points, plausibility = [], []
    for c in model["corpora"]:
        stage, mtype = c["stage"], c["manifest_type"]
        rows = [r for r in ledger["rows"] if r["model"] == model_id and r["stage"] == stage]
        if mtype == "by-design":
            manifest_points.append(f"{stage}: by design, it trains on {len(c['splits'])} of its own evaluation "
                                   "datasets; each records what the paper holds out for testing.")
            plausibility.append(f"{stage}: {len(rows)} by design, none counted as exposure.")
            continue
        if mtype in ("C-vague", "D"):
            manifest_points.append(f"{stage}: no training-data list was published.")
        elif mtype == "B":
            pointer = c.get("pointer") or {}
            manifest_points.append(f"{stage}: {pointer.get('corpus', 'a public corpus')} {pointer.get('release', '')}".rstrip()
                                   + ", a superset of what the model used (it can rule datasets out, never in).")
        else:
            sources = ", ".join(f"{s['name']}" + (f" ({s['records']:,} entries)" if s.get("records") else "")
                                for s in c["sources"])
            line = f"{stage}: {sources}"
            totals = c.get("totals") or {}
            if totals.get("cells"):
                line += f"; {totals['cells']:,} cells in all"
            if c.get("joined_through"):
                line += f"; dataset ids joined to collections and DOIs through {' and '.join(c['joined_through'])}"
            manifest_points.append(line + ".")
        if unresolved := c.get("unresolved_entries"):
            manifest_points.append(f"{stage}: {len(unresolved)} entries can't be searched exactly, so absence stays "
                                   "INCONCLUSIVE until they are resolved.")
        present = sorted(name(r["dataset"]) for r in rows if r["verdict"] == "PRESENT")
        counts = {v: sum(r["verdict"] == v for r in rows) for v in ("NOT PRESENT", "INCONCLUSIVE", "NOT CHECKABLE")}
        parts = [f"PRESENT: {', '.join(present)}" if present else "PRESENT: none"]
        parts += [f"{v}: {n}" for v, n in counts.items() if n]
        plausibility.append(f"{stage}: " + "; ".join(parts) + ".")

    evals = list(dict.fromkeys(e["dataset"] for e in model["evaluated_on"]))
    return {
        "paper": paper_points,
        "manifest": manifest_points,
        "evaluated_on": [f"{len(evals)} datasets: {', '.join(name(d) for d in evals)}."],
        "judgment_calls": judgment_calls(ledger, model_id),
        "plausibility": plausibility,
    }
