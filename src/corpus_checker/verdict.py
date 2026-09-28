"""The verdict function — the verdict rules of docs/RULES.md, in code.

Mirrors the validator's guards on purpose: the engine must never emit a verdict that
`corpus-checker validate` would reject (tests/test_canary.py checks this).
There is NO model-level verdict. This decides one (model, stage, dataset) pair.

`decide_from()` takes plain values only, so the site's JavaScript port can be tested
against the same cases (tests/fixtures/lookup_vectors.json). Reason codes are stable;
reason text is for people.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .match import Match
from .registry import EXACT_KEYS

PRESENT, NOT_PRESENT, INCONCLUSIVE, NOT_CHECKABLE = "PRESENT", "NOT PRESENT", "INCONCLUSIVE", "NOT CHECKABLE"

PAPER_LEVEL_DISCLAIMER = (
    "PMIDs and DOIs identify papers, not datasets. One paper can publish several datasets "
    "(Zheng 2017 released a mouse-brain dataset and the PBMC data; Replogle 2022 released K562 "
    "and RPE1 screens). A match on a PMID or DOI alone cannot tell which of them a model trained "
    "on. Search by accession (GSE…, E-MTAB…, a CELLxGENE ID) whenever you can."
)


@dataclass(frozen=True)
class Decision:
    verdict: str
    reason: str | None = None
    code: str = ""


def decide_from(*, manifest_type: str, can_prove_presence: bool, checkable: bool = True,
                keys_attempted: Iterable[str] = (), keys_hit: Iterable[str] = (),
                listed_accessions: Iterable[str] = (), requires_keys: Iterable[str] = (),
                unconfirmed_sources: Iterable[str] = (), unsearched_sources: Iterable[str] = (),
                keys_absent: Iterable[str] = (), keys_hit_confirmed: Iterable[str] | None = None) -> Decision:
    """keys_absent: required keys the catalog records as not existing for this dataset, with a basis —
    they count as covered for NOT PRESENT, never as searched. keys_hit_confirmed: the keys hit on rows
    from confirmed sources; None means "not known", which is read conservatively (no confirmed hit)
    whenever a source is unconfirmed."""
    attempted, hit = set(keys_attempted), set(keys_hit)
    requires, listed = set(requires_keys), sorted(listed_accessions)
    unconfirmed, unsearched = list(unconfirmed_sources), list(unsearched_sources)
    absent = set(keys_absent) - attempted

    if manifest_type in ("C-vague", "D") or not checkable:
        return Decision(NOT_CHECKABLE, "no manifest was published to search", "no_manifest")

    if unconfirmed:
        # A match inside a confirmed file is still a match; an unconfirmed file can only ever
        # weaken absence. So presence is decided on the confirmed-file hits alone.
        confirmed_hit = set(keys_hit_confirmed or ()) & hit
        if not confirmed_hit & EXACT_KEYS:
            state = "matched" if hit else "did not match"
            where = " (only in an unconfirmed file)" if hit else ""
            return Decision(INCONCLUSIVE, f"the manifest {state}{where}, but source(s) {unconfirmed} are not confirmed "
                                          "as the paper's file (supplied, no url_hash / totals / author confirmation)",
                            "unconfirmed_source")
        hit = confirmed_hit

    if hit & EXACT_KEYS:
        paper_only = "accession" not in hit
        if not can_prove_presence:
            extra = " The match is also on the paper, not the dataset." if paper_only else ""
            return Decision(INCONCLUSIVE, "found in the pointed-to corpus, which the model trained on a subset of — "
                                          "a superset cannot prove presence." + extra, "superset")
        if paper_only:
            where = f"the manifest lists {', '.join(listed)}" if listed else "the manifest row names no accession"
            return Decision(INCONCLUSIVE, f"same publication, matched on {'/'.join(k.upper() for k in sorted(hit & EXACT_KEYS))} only — "
                                          f"{where}; confirm it is the same dataset", "same_publication")
        return Decision(PRESENT, None, "present")   # presence in part of a manifest is presence
    if hit:
        return Decision(INCONCLUSIVE, f"matched on {sorted(hit)} only — not an exact identifier", "weak_only")

    if unsearched:
        return Decision(INCONCLUSIVE, f"no match, but manifest source(s) {unsearched} were not searched", "unsearched")
    if not attempted & EXACT_KEYS:
        return Decision(INCONCLUSIVE, "no exact key could be attempted: the dataset has no confirmed identifier "
                                      "of a kind this manifest carries", "no_exact_key")
    if missing := requires - attempted - absent:
        return Decision(INCONCLUSIVE, f"no match on {sorted(attempted)}, but this manifest needs {sorted(requires)} "
                                      f"attempted before absence can be claimed; the dataset has no confirmed "
                                      f"{'/'.join(sorted(missing))} identifier, and none is recorded as not existing",
                        "missing_required_key")
    return Decision(NOT_PRESENT, None, "not_present")


def decide(*, manifest_type: str, can_prove_presence: bool, match: Match | None,
           requires_keys: frozenset[str] = frozenset(), unconfirmed_sources: tuple[str, ...] = (),
           unsearched_sources: tuple[str, ...] = (), keys_absent: frozenset[str] = frozenset()) -> Decision:
    if match is None:
        return decide_from(manifest_type=manifest_type, can_prove_presence=can_prove_presence, checkable=False)
    return decide_from(manifest_type=manifest_type, can_prove_presence=can_prove_presence,
                       keys_attempted=match.keys_attempted, keys_hit=match.keys_hit,
                       listed_accessions=match.listed_accessions, requires_keys=requires_keys,
                       unconfirmed_sources=unconfirmed_sources, unsearched_sources=unsearched_sources,
                       keys_absent=keys_absent,
                       keys_hit_confirmed=match.keys_hit_outside(unconfirmed_sources))
