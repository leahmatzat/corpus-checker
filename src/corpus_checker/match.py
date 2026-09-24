"""Exact matching of one benchmark dataset against a manifest's canonical records.

★ Every key in `keys` is attempted and the hits for each are reported. Never
short-circuit on the first hit: scFoundation's study table has 161/522 blank PMIDs
and 134/656 projects appear only in the sample table, so which keys hit IS evidence.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from . import normalize
from .registry import identifier_key, usable_identifiers
from .resolvers import CanonicalRecord


class RecordIndex:
    """Records indexed by identifier token, plus sample rows by project for evidence."""

    def __init__(self, records: Iterable[CanonicalRecord]):
        self.records = list(records)
        self._by: dict[str, dict[str, list[CanonicalRecord]]] = {k: defaultdict(list) for k in ("accession", "pmid", "doi")}
        self._samples_by_project: dict[str, list[CanonicalRecord]] = defaultdict(list)
        for r in self.records:
            for a in r.accessions:
                self._by["accession"][a].append(r)
            for p in r.pmids:
                self._by["pmid"][p].append(r)
            for d in r.dois:
                self._by["doi"][d].append(r)
            if r.sample and r.project:
                self._samples_by_project[r.project].append(r)

    def lookup(self, key: str, value: str) -> list[CanonicalRecord]:
        return self._by[key].get(value, [])

    def samples_in(self, project: str) -> list[CanonicalRecord]:
        return self._samples_by_project.get(project, [])


@dataclass(frozen=True)
class Match:
    dataset: str
    keys_attempted: tuple[str, ...]
    hits: Mapping[str, tuple[CanonicalRecord, ...]]
    evidence: tuple[CanonicalRecord, ...] = field(default=())   # sample-level rows, deduplicated

    @property
    def keys_hit(self) -> tuple[str, ...]:
        return tuple(k for k in self.keys_attempted if self.hits.get(k))

    @property
    def any_hit(self) -> bool:
        return bool(self.keys_hit)

    @property
    def sample_ids(self) -> tuple[str, ...]:
        ids = []
        for r in self.evidence:
            tokens = normalize.accessions(r.sample)
            ids.append(tokens[0] if tokens else r.sample)
        return tuple(sorted(dict.fromkeys(ids)))

    @property
    def cells(self) -> int | None:
        counts = [r.cells for r in self.evidence if r.cells is not None]
        return sum(counts) if counts else None

    @property
    def overlap_level(self) -> str | None:
        if not self.any_hit:
            return None
        return "sample" if self.evidence else "study"


def targets(dataset: dict, accession_types: list[str] | None = None) -> dict[str, set[str]]:
    """The dataset's usable identifier values, grouped by match key."""
    out: dict[str, set[str]] = defaultdict(set)
    for ident in usable_identifiers(dataset, accession_types):
        key = identifier_key(ident["type"])
        value = str(ident["value"]).strip()
        if key == "accession":
            out[key].add(normalize.accession(value))
        elif key == "doi":
            out[key].add(normalize.doi(value))
        elif key == "pmid":
            out[key].add(value)
    return out


def match(index: RecordIndex, dataset: dict, keys: Sequence[str],
          accession_types: list[str] | None = None) -> Match:
    wanted = targets(dataset, accession_types)
    hits: dict[str, tuple[CanonicalRecord, ...]] = {}
    for key in keys:   # every key, always
        found: dict[tuple[str, int], CanonicalRecord] = {}
        for value in sorted(wanted.get(key, ())):
            for r in index.lookup(key, value):
                found[(r.source, r.row)] = r
        hits[key] = tuple(found.values())

    # Evidence = sample rows. A hit on a sample row counts itself; a hit on a study row
    # counts the sample rows of that project. Never expand a sample hit to its whole
    # project — a dataset declared by GSM would otherwise swallow its siblings.
    evidence: dict[tuple[str, int], CanonicalRecord] = {}
    for records in hits.values():
        for r in records:
            if r.sample:
                evidence[(r.source, r.row)] = r
            elif r.project:
                for s in index.samples_in(r.project):
                    evidence[(s.source, s.row)] = s
    return Match(dataset=dataset["id"], keys_attempted=tuple(keys), hits=hits,
                 evidence=tuple(sorted(evidence.values(), key=lambda r: (r.source, r.row))))
