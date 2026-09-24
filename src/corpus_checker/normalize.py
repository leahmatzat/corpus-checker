"""Turn raw manifest cells into identifier tokens that can be compared exactly.

Never substring-match a raw cell. Real manifests carry:
  - source prefixes:   GEO-GSE133344, DISCO-GSE…, AE-E-MTAB-11536, DISCO-HRA000…
  - several IDs a cell: "31067475 || 33521016", "31924475;34099645"
  - the wrong kind:     "doi:10.1016/j.jhep.2020.05.039" in a PubMed ID column
  - spreadsheet floats: 31395745.0
and `"GSE9054" in "GSE90546"` is True. Tokens are extracted whole and compared with ==.
"""
from __future__ import annotations

import re

_ACCESSION_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"GS[EM]\d+"                 # GEO series / sample
    r"|E-[A-Z]{4}-\d+"           # ArrayExpress / BioStudies
    r"|[SED]R[APRSX]\d+"         # SRA / ENA / DDBJ study, project, run, sample, experiment
    r"|PRJ[NED][A-Z]?\d+"        # BioProject
    r"|EGA[SD]\d+"               # EGA study / dataset
    r"|SCP\d+"                   # Broad Single Cell Portal
    r"|HRA\d+"                   # GSA-Human
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"   # CELLxGENE collection / dataset UUID
    r")(?![0-9A-Za-z])",
    re.IGNORECASE,
)
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s|;,]+", re.IGNORECASE)
_PMID_RE = re.compile(r"(?<![\d.])\d{1,9}(?![\d.])")


def _text(cell: object) -> str:
    if cell is None:
        return ""
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    s = str(cell).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


_UUID_RE = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)


def accession(value: str) -> str:
    """Canonical case: UUIDs lower, repository accessions upper."""
    value = value.strip()
    return value.lower() if _UUID_RE.match(value) else value.upper()


def accessions(cell: object) -> tuple[str, ...]:
    """Every repository accession in a cell, canonical case, in order, without duplicates."""
    return tuple(dict.fromkeys(accession(m) for m in _ACCESSION_RE.findall(_text(cell))))


def dois(cell: object) -> tuple[str, ...]:
    return tuple(dict.fromkeys(d.rstrip(".").lower() for d in _DOI_RE.findall(_text(cell))))


def pmids(cell: object) -> tuple[str, ...]:
    """PMIDs in a PubMed-ID cell. DOIs are removed first so their digits are not read as PMIDs."""
    rest = _DOI_RE.sub(" ", _text(cell))
    return tuple(dict.fromkeys(_PMID_RE.findall(rest)))


def text(cell: object) -> str:
    return _text(cell)


def doi(value: str) -> str:
    return value.strip().lower()
