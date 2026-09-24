"""The ledger is a pure inversion of the registry — and carries no model-level score."""
from __future__ import annotations

import json

from corpus_checker.ledger import LEDGER_VERSION, build_ledger
from corpus_checker.registry import load_yaml, registry_paths

from conftest import REPO

LEDGER = build_ledger(REPO)


def test_every_finding_becomes_exactly_one_row():
    n = sum(len(c["result"]["findings"]) for p in registry_paths(REPO) for c in load_yaml(p)["corpora"])
    assert len(LEDGER["rows"]) == n


def test_norman_reuse_is_surfaced():
    reuse = LEDGER["datasets"]["norman2019"]["reuse"]
    assert {"in_training_of": "scfoundation", "stage": "pretraining", "evaluated_by": "scgpt"} in reuse


def test_relation_comes_from_evaluated_on():
    rows = {(r["dataset"], r["model"]): r for r in LEDGER["rows"]}
    assert rows[("norman2019", "scfoundation")]["relation"] == "self-eval"
    assert rows[("baron2016", "geneformer-30m")]["relation"] == "catalog"


def test_no_model_level_score_anywhere():
    """Invariant 4: verdicts are per (model, stage, dataset). No grade, rank or total per model."""
    forbidden = {"score", "grade", "rank", "overall", "clean", "contaminated"}
    for model in LEDGER["models"].values():
        assert not forbidden & {k.lower() for k in model}


def test_json_round_trip_and_version():
    assert json.loads(json.dumps(LEDGER))["ledger_version"] == LEDGER_VERSION


def test_drafts_are_marked():
    assert LEDGER["models"]["scfoundation"]["draft"] is False
    assert LEDGER["models"]["scgpt"]["draft"] is True
