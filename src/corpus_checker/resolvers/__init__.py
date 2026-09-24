"""One resolver per manifest shape. They differ in how they read; they agree on CanonicalRecord.

Locating a manifest file and verifying its sha256 is shared
(check.py), not per resolver: publisher files are downloaded by hand, so a resolver
only ever reads bytes that have already been matched to the registry's hash.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Iterator, Protocol


@dataclass(frozen=True)
class CanonicalRecord:
    """One row of somebody's training corpus, normalised.

    Identifier fields are tuples because real cells hold several ("a || b").
    """
    accessions: tuple[str, ...] = ()
    pmids: tuple[str, ...] = ()
    dois: tuple[str, ...] = ()
    title: str | None = None
    citation: str | None = None
    project: str | None = None      # join key between one manifest's tables, as written
    sample: str | None = None       # set on sample-level rows only
    cells: int | None = None
    source: str = ""                # manifest source name
    row: int = 0                    # 1-based data row in that source, for evidence


class Resolver(Protocol):
    manifest_type: ClassVar[str]         # "A" | "A+" | "B" | "C-named"
    can_prove_presence: ClassVar[bool]   # False for B — a superset cannot prove presence

    def records(self, source: dict, path: Path) -> Iterator[CanonicalRecord]:
        """Stream the source's rows as canonical records."""


def get_resolver(name: str) -> Resolver:
    from .table import SupersetTableResolver, TableResolver

    resolvers = {
        "table": TableResolver,
        "xlsx_table": TableResolver,
        "xlsx_two_table": TableResolver,          # scFoundation: sample table + study table, joined on project
        "census_snapshot": SupersetTableResolver,  # scGPT: committed snapshot of a CELLxGENE census release
    }
    try:
        return resolvers[name]()
    except KeyError:
        raise ValueError(f"no resolver named {name!r} (known: {sorted(resolvers)})") from None
