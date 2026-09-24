"""The verdict function — the verdict rules of docs/RULES.md, in code.

Mirrors the validator's guards on purpose: the engine must never emit a verdict that
`corpus-checker validate` would reject (tests/test_canary.py checks this).
There is NO model-level verdict. This decides one (model, stage, dataset) pair.
"""
from __future__ import annotations

from dataclasses import dataclass

from .match import Match
from .registry import EXACT_KEYS

PRESENT, NOT_PRESENT, INCONCLUSIVE, NOT_CHECKABLE = "PRESENT", "NOT PRESENT", "INCONCLUSIVE", "NOT CHECKABLE"


@dataclass(frozen=True)
class Decision:
    verdict: str
    reason: str | None = None


def decide(*, manifest_type: str, can_prove_presence: bool, match: Match | None,
           requires_keys: frozenset[str] = frozenset(), unconfirmed_sources: tuple[str, ...] = (),
           unsearched_sources: tuple[str, ...] = ()) -> Decision:
    if manifest_type in ("C-vague", "D") or match is None:
        return Decision(NOT_CHECKABLE, "no manifest was published to search")

    attempted = set(match.keys_attempted)
    hit = set(match.keys_hit)

    if unconfirmed_sources:
        state = "matched" if hit else "did not match"
        return Decision(INCONCLUSIVE, f"the manifest {state}, but source(s) {list(unconfirmed_sources)} are not "
                                      "confirmed as the paper's file (supplied, no url_hash / totals / author confirmation)")

    if hit & EXACT_KEYS:
        if can_prove_presence:
            return Decision(PRESENT)   # presence in part of a manifest is presence
        return Decision(INCONCLUSIVE, "found in the pointed-to corpus, which the model trained on a subset of — "
                                      "a superset cannot prove presence")
    if hit:
        return Decision(INCONCLUSIVE, f"matched on {sorted(hit)} only — not an exact identifier")

    if unsearched_sources:
        return Decision(INCONCLUSIVE, f"no match, but manifest source(s) {list(unsearched_sources)} were not searched")
    if not attempted & EXACT_KEYS:
        return Decision(INCONCLUSIVE, "no exact key could be attempted: the dataset has no confirmed identifier "
                                      "of a kind this manifest carries")
    if missing := requires_keys - attempted:
        return Decision(INCONCLUSIVE, f"no match on {sorted(attempted)}, but this manifest needs {sorted(requires_keys)} "
                                      f"attempted before absence can be claimed; the dataset has no confirmed "
                                      f"{'/'.join(sorted(missing))} identifier")
    return Decision(NOT_PRESENT)
