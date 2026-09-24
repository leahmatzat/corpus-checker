"""The ledger: registry/*.yaml inverted on dataset. DERIVED — never authored, never committed.

It answers the question no per-model check can: the same dataset is training data for
one model and evaluation data for another. That cross-model view is what someone
comparing models on a shared benchmark needs.

`build_ledger()` returns a plain JSON-able dict. Its shape is the contract the static
site renders (LEDGER_VERSION bumps on any breaking change):

    {
      "ledger_version": 1,
      "models":   {model_id: {model, version, model_class, paper, draft, verified_by,
                              verified_date, disputed, evaluated_on: [{dataset, task,
                              obtained_from}], corpora: [{stage, name, manifest_type,
                              identifier_type, quote, pointer, sources, sources_searched,
                              caveats}]}},
      "datasets": {dataset_id: {name, kind, organism, identifiers, sources, note,
                                evaluated_by: [model_id], exposure: [row], reuse: [...]}},
      "rows":     [{dataset, model, stage, relation, verdict, keys_attempted, matched_on,
                    overlap_level, samples, sample_ids, cells, reason, note}]
    }

There is NO model-level score anywhere in it, by design.
"""
from __future__ import annotations

from pathlib import Path

from .registry import load_catalog, load_yaml, registry_paths, relation

LEDGER_VERSION = 1
_ROW_FIELDS = ("keys_attempted", "matched_on", "match_level", "identity_note", "overlap_level", "samples",
               "sample_ids", "cells", "reason", "note")


def build_ledger(root: Path) -> dict:
    catalog = load_catalog(root)
    models, rows = {}, []

    for path in registry_paths(root):
        e = load_yaml(path)
        prov = e["provenance"]
        models[e["id"]] = {
            "model": e["model"],
            "version": e.get("version"),
            "model_class": e["model_class"],
            "paper": e["paper"],
            "draft": not prov["verified_by"].strip(),
            "verified_by": prov["verified_by"] or None,
            "verified_date": prov["verified_date"],
            "disputed": e.get("disputed"),
            "evaluated_on": e["evaluated_on"],
            "corpora": [{
                "stage": c["stage"],
                "name": c.get("name"),
                "manifest_type": c["manifest"]["type"],
                "identifier_type": c["manifest"].get("identifier_type"),
                "quote": c["manifest"]["quote"],
                "pointer": c["manifest"].get("pointer"),
                "sources": [{k: s.get(k) for k in ("name", "url", "sha256", "acquired", "confirmation", "records")}
                            for s in c["manifest"].get("sources", [])],
                "sources_searched": c["result"]["sources_searched"],
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
        exposure = [r for r in d_rows if r["verdict"] == "PRESENT"]
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
