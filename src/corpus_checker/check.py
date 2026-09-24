"""Run a registry entry's check from its manifest files, and compare with what the registry claims.

Publisher manifests are never committed. A check locates each source on disk, proves
by sha256 that it is the file the registry scored, and only then reads it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from .match import RecordIndex, match
from .registry import EXACT_KEYS, confirmed_keys, relation
from .resolvers import get_resolver
from .verdict import Decision, decide

Locator = Callable[[dict], Path]


class SourceError(Exception):
    """A manifest file is missing, or is not the file the registry scored."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def locate_in_dir(directory: Path) -> Locator:
    """Find each source by its URL's file name in `directory` and verify its sha256."""
    def locate(source: dict) -> Path:
        name = Path(unquote(urlparse(source["url"]).path)).name
        path = Path(directory) / name
        if not path.is_file():
            raise SourceError(f"{source['name']}: expected {name} in {directory}")
        if (digest := sha256_file(path)) != source["sha256"]:
            raise SourceError(f"{source['name']}: {path} has sha256 {digest[:12]}…, the registry scored "
                              f"{source['sha256'][:12]}… — not the same file")
        return path
    return locate


def locate_extracts(extracts: dict[str, Path]) -> Locator:
    """Derived CSV extracts (tests/fixtures), matched to sources by the parent hash in their header."""
    def locate(source: dict) -> Path:
        path = extracts.get(source["name"])
        if path is None:
            raise SourceError(f"no extract for {source['name']!r}")
        header = [line for line in Path(path).read_text(encoding="utf-8").splitlines()[:5] if line.startswith("#")]
        if not any(f"derived-from-sha256: {source['sha256']}" in line for line in header):
            raise SourceError(f"{path} does not declare derived-from-sha256: {source['sha256']}")
        return Path(path)
    return locate


def with_snapshots(root: Path, fallback: Locator | None = None) -> Locator:
    """Sources with a committed `snapshot` are read from the repo (sha256-verified); others go to `fallback`."""
    def locate(source: dict) -> Path:
        if snap := source.get("snapshot"):
            path = Path(root) / snap
            if not path.is_file():
                raise SourceError(f"{source['name']}: committed snapshot {snap} is missing")
            if (digest := sha256_file(path)) != source["sha256"]:
                raise SourceError(f"{source['name']}: {snap} has sha256 {digest[:12]}…, the registry scored {source['sha256'][:12]}…")
            return path
        if fallback is None:
            raise SourceError(f"{source['name']}: not committed — pass --manifests DIR holding {source['url']}")
        return fallback(source)
    return locate


@dataclass(frozen=True)
class FindingResult:
    dataset: str
    relation: str
    decision: Decision
    keys_attempted: tuple[str, ...]
    matched_on: tuple[str, ...]
    overlap_level: str | None
    sample_ids: tuple[str, ...]
    cells: int | None

    @property
    def verdict(self) -> str:
        return self.decision.verdict

    def as_finding(self) -> dict:
        """The registry `findings[]` form — for drafting an entry, never for auto-verifying one."""
        f: dict = {"dataset": self.dataset, "verdict": self.verdict}
        if self.verdict != "NOT CHECKABLE":
            f["keys_attempted"] = list(self.keys_attempted)
        if self.matched_on:
            f["matched_on"] = list(self.matched_on)
        if self.verdict == "PRESENT":
            f["overlap_level"] = self.overlap_level
            f["samples"] = len(self.sample_ids)
            f["sample_ids"] = list(self.sample_ids)
            if self.cells is not None:
                f["cells"] = self.cells
        if self.decision.reason:
            f["reason"] = self.decision.reason
        return f


@dataclass(frozen=True)
class CorpusCheck:
    stage: str
    sources_searched: tuple[str, ...]
    index: RecordIndex | None
    results: tuple[FindingResult, ...]


def check_corpus(entry: dict, corpus: dict, catalog: dict[str, dict], locate: Locator,
                 dataset_ids: list[str] | None = None) -> CorpusCheck:
    manifest = corpus["manifest"]
    ids = dataset_ids if dataset_ids is not None else sorted(catalog)

    if manifest["type"] in ("C-vague", "D"):
        results = tuple(FindingResult(d, relation(entry, d), decide(manifest_type=manifest["type"], can_prove_presence=False, match=None),
                                      (), (), None, (), None) for d in ids)
        return CorpusCheck(corpus["stage"], (), None, results)

    resolver = get_resolver(manifest["resolver"])
    sources = manifest.get("sources", [])
    records, searched = [], []
    for source in sources:
        records.extend(resolver.records(source, locate(source)))   # raises SourceError: never a silent partial search
        searched.append(source["name"])
    index = RecordIndex(records)

    unconfirmed = tuple(s["name"] for s in sources if s["confirmation"]["method"] == "none")
    exact_available = set(manifest.get("keys_available", [])) & EXACT_KEYS
    requires = frozenset(manifest.get("requires_keys", []))
    accession_types = manifest.get("accession_types")

    results = []
    for d in ids:
        dataset = catalog[d]
        keys = sorted(confirmed_keys(dataset, accession_types) & exact_available)
        m = match(index, dataset, keys, accession_types)
        decision = decide(manifest_type=manifest["type"], can_prove_presence=resolver.can_prove_presence,
                          match=m, requires_keys=requires, unconfirmed_sources=unconfirmed)
        results.append(FindingResult(
            dataset=d, relation=relation(entry, d), decision=decision,
            keys_attempted=tuple(keys), matched_on=m.keys_hit, overlap_level=m.overlap_level,
            sample_ids=m.sample_ids, cells=m.cells,
        ))
    return CorpusCheck(corpus["stage"], tuple(searched), index, tuple(results))


@dataclass(frozen=True)
class Discrepancy:
    dataset: str
    field: str
    registry: object
    engine: object


def compare(corpus: dict, check: CorpusCheck) -> list[Discrepancy]:
    """Where the registry's recorded findings differ from a fresh run. Only datasets the registry records."""
    engine = {r.dataset: r for r in check.results}
    out = []
    for f in corpus["result"]["findings"]:
        r = engine.get(f["dataset"])
        if r is None:
            continue
        pairs = [("verdict", f["verdict"], r.verdict),
                 ("keys_attempted", sorted(f.get("keys_attempted", [])), sorted(r.keys_attempted))]
        if f["verdict"] == "PRESENT" or r.verdict == "PRESENT":
            pairs += [("matched_on", sorted(f.get("matched_on", [])), sorted(r.matched_on)),
                      ("cells", f.get("cells"), r.cells),
                      ("sample_ids", sorted(f.get("sample_ids", [])), list(r.sample_ids))]
        out += [Discrepancy(f["dataset"], name, a, b) for name, a, b in pairs if a != b]
    return out
