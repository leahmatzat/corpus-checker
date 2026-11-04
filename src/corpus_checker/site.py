"""Generates the static site from `build_ledger()`. DERIVED — never a data source.

Pages: `index.html` (reported evals by model + the reuse table), `all-datasets.html` (the full
datasets x models grid), `models/<id>.html`, `datasets/<id>.html`, `about.html`
(docs/DATA_FLOW.md rendered), `lookup.html` and one `style.css`.
No server, no external assets besides the optional Mermaid script on the about page,
no analytics, no cookies.

Content rules this module exists to enforce: exposure, not effect · no model-level score,
grade, rank or sort-by-cleanliness · the four verdicts are always explained · a draft model
carries a visible label everywhere it appears · every manifest source, quote, caveat and
finding is shown with its evidence · every page links back to a correction issue and to its
source YAML.

Determinism: every iteration over the ledger is done in an explicitly sorted order (never a
bare `set`, never wall-clock time) so that building twice produces byte-identical files.
"""
from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote as _urlquote

import jinja2
import markdown as _markdown

from .ledger import build_ledger, review_points
from .lookup import build_lookup_index
from .registry import REVIEW_CHECKS
from .verdict import PAPER_LEVEL_DISCLAIMER

# --------------------------------------------------------------------------------- constants

PLACEHOLDER_REPO_URL = "https://github.com/OWNER/corpus-checker"

EXPOSURE_NOTE = ("Exposure, not effect: overlap does not by itself mean a reported number is "
                  "inflated. NOT CHECKABLE is a finding about what was published, not an error.")

INTRO_PARAGRAPH = ("The tool checks whether evaluation datasets are contained in single-cell foundation "
                   "model training sets.")

# Kept in this order deliberately — it is the order the legend and "how to read a verdict"
# render in, and matches docs/DATA_FLOW.md's own verdict table.
VERDICT_EXPLANATION = {
    "PRESENT": "An exact match on accession, PMID or DOI, with the matched samples or cells recorded.",
    "NOT PRESENT": "Every required key was attempted against every searched source, and none matched.",
    "INCONCLUSIVE": "The match is weak — a title/keyword match, a match inside a superset corpus, or a "
                    "match against an unconfirmed file. The reason is always shown.",
    "NOT CHECKABLE": "The paper did not publish enough to answer the question. A finding about what was "
                      "published, not an error.",
}

MANIFEST_TYPE_EXPLANATION = {
    "A": "A static list of the training sources, published as a file (e.g. a supplementary table).",
    "A+": "A static list of the training sources, published as a file, with the corpus itself also downloadable.",
    "B": "A pointer to a versioned external corpus of which the model used only an unstated subset. "
         "This kind of manifest can rule a dataset out, but can never confirm one is present.",
    "C-named": "Prose in the paper that names sources precisely enough to resolve to a specific record.",
    "C-vague": "Prose in the paper that describes sources only in general terms — not specific enough "
               "to check against any one dataset.",
    "D": "No description of training sources was published.",
    "by-design": "No list to search: by design, this stage trains on the model's own evaluation datasets and "
                 "tests on what each holds out. Its findings record that, and are never counted as exposure.",
}

# The few words under each stage on the home page; the full explanation is on the model page.
MANIFEST_TYPE_SHORT = {
    "A": "file list",
    "A+": "file list + corpus",
    "B": "superset",
    "C-named": "named in prose",
    "C-vague": "prose only",
    "D": "not published",
    "by-design": "no list to search",
}

# Home page columns. Every reported eval lands in exactly one, by its finding for that stage.
ANSWER_COLUMN = {
    "PRESENT": "present",
    "NOT PRESENT": "not_present",
    "INCONCLUSIVE": "unclear",
    "NOT CHECKABLE": "unclear",
}
UNCLEAR_SUFFIX = {"INCONCLUSIVE": "inconclusive", "NOT CHECKABLE": "not checkable", None: "not checked"}

# A stage summary names the reported evals found in the corpus and describes the rest in words —
# never a count by verdict, which would read as a score.
REST_LABEL = {"NOT PRESENT": "not in the training corpus", "INCONCLUSIVE": "inconclusive",
              "NOT CHECKABLE": "not checkable", None: "not checked"}

# Why a finding is INCONCLUSIVE / NOT CHECKABLE, in a few words, keyed by how the engine's reason
# (verdict.py) begins. Anything unmatched gets no phrase; the full reason is always on the model page.
_WHY = (
    (lambda r: "of this list cannot be searched exactly" in r, "unresolved"),
    (lambda r: r.startswith("no exact key could be attempted"), "the catalog has no identifier of a kind the list carries"),
    (lambda r: r.startswith("no match on") and "this manifest needs" in r,
     "the catalog lacks an identifier the list needs before absence can be claimed"),
    (lambda r: r.startswith("no match, but manifest source"), "not every file of the list was searched"),
    (lambda r: r.startswith("same publication"), "the match is on the paper, not the dataset"),
    (lambda r: r.startswith("matched on") and "not an exact identifier" in r, "the match is not on an exact identifier"),
    (lambda r: r.startswith("the manifest") and "not confirmed" in r, "the list file isn't confirmed yet"),
    (lambda r: r.startswith("no manifest was published"), "no training-data list was published"),
)

# "All datasets" grid cells are narrow: a short word, with the full verdict in the title.
GRID_SHORT = {"PRESENT": "Yes", "NOT PRESENT": "No", "INCONCLUSIVE": "?", "NOT CHECKABLE": "n/c"}

# Row groups on the grid, in this order; any other kind follows alphabetically, then "other".
KIND_ORDER = ("perturbation", "observational", "multimodal", "spatial")

# Plain names for the places a paper can describe its training data (registry `discovery`).
DISCOVERY_LABEL = {
    "data_availability": "the data availability statement",
    "code_availability": "the code availability statement",
    "supplementary_files": "the supplementary files",
    "methods": "the Methods section",
    "github_repo": "the GitHub repository",
    "hf_model_card": "the Hugging Face model card",
    "hf_dataset_card": "the Hugging Face dataset card",
    "archive_deposits": "archive deposits (Zenodo, Figshare and the like)",
    "preprint_vs_published": "the preprint versus the published version",
}

CONFIRMATION_EXPLANATION = {
    "url_hash": "downloaded from the publisher or repo URL; the hash is of those bytes",
    "api_snapshot": "a deterministic, re-runnable export of a versioned corpus's own API",
    "totals": "the file's contents add up to counts printed in the paper",
    "author": "the authors confirmed it in writing",
    "none": "supplied, not yet confirmed",
}

# Short names for the five review checks (registry.REVIEW_CHECKS holds what each one covers).
CHECK_LABEL = {
    "paper": "Paper",
    "manifest": "Training-data list",
    "evaluated_on": "Evaluation list",
    "judgment_calls": "Judgment calls",
    "plausibility": "Plausibility",
}

# Home page sections, by model_class.
MODEL_GROUPS = (
    ("foundation", "Foundation models", None),
    ("task-specific", "Task-specific models",
     "These train directly on task data rather than on a pretraining corpus. Their training stage uses part "
     "of each evaluation dataset by design, which is not exposure."),
)

MODEL_CLASS_EXPLANATION = {
    "foundation": "has a pretraining corpus distinct from its evaluation data",
    "task-specific": "trains directly on task data; may have no pretraining corpus at all",
}

# datasets/ identifier type -> URL builder. Only types with a well-known, stable URL scheme are
# linked (GEO, PubMed, DOI and the CELLxGENE collection are required; the rest are a bonus).
# Anything not listed here (e.g. `hca`, `figshare`) is shown as plain text rather than guessing
# a wrong link.
_IDENTIFIER_URL = {
    "geo": lambda v: f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={_urlquote(v)}",
    "pmid": lambda v: f"https://pubmed.ncbi.nlm.nih.gov/{_urlquote(v)}/",
    "doi": lambda v: f"https://doi.org/{_urlquote(v, safe='/')}",
    "cellxgene_collection": lambda v: f"https://cellxgene.cziscience.com/collections/{_urlquote(v)}",
    "cellxgene_dataset": lambda v: f"https://cellxgene.cziscience.com/e/{_urlquote(v)}.cxg/",
    "arrayexpress": lambda v: f"https://www.ebi.ac.uk/biostudies/arrayexpress/studies/{_urlquote(v)}",
    "sra": lambda v: f"https://www.ncbi.nlm.nih.gov/sra/?term={_urlquote(v)}",
    "bioproject": lambda v: f"https://www.ncbi.nlm.nih.gov/bioproject/{_urlquote(v)}",
    "ega": lambda v: f"https://ega-archive.org/{'studies' if v.upper().startswith('EGAS') else 'datasets'}/{_urlquote(v)}",
    "scp": lambda v: f"https://singlecell.broadinstitute.org/single_cell/study/{_urlquote(v)}",
    "hf_dataset": lambda v: f"https://huggingface.co/datasets/{_urlquote(v, safe='/')}",
    "zenodo": lambda v: f"https://zenodo.org/records/{_urlquote(v)}",
    "synapse": lambda v: f"https://www.synapse.org/#!Synapse:{_urlquote(v)}",
    "url": lambda v: v,
}

_TEMPLATES_DIR = Path(__file__).parent / "site_templates"
_MERMAID_FENCE_RE = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)


# --------------------------------------------------------------------------------- small helpers

def _identifier_url(id_type: str, value) -> str | None:
    builder = _IDENTIFIER_URL.get(id_type)
    return builder(str(value)) if builder else None


def _correction_url(repo_url: str, name: str) -> str:
    title = _urlquote(f"[correction] {name}", safe="")
    return f"{repo_url}/issues/new?template=correction.yml&title={title}"


def _add_dataset_url(repo_url: str) -> str:
    """Static part of the add-dataset link; lookup.js appends the per-query identifiers/results."""
    title = _urlquote("[add-dataset] ", safe="")
    return f"{repo_url}/issues/new?template=add-dataset.yml&title={title}"


def _source_url(repo_url: str, kind: str, id_: str) -> str:
    folder = "registry" if kind == "model" else "datasets"
    return f"{repo_url}/blob/main/{folder}/{id_}.yaml"


def _rules_url(repo_url: str) -> str:
    return f"{repo_url}/blob/main/docs/RULES.md"


def _evidence_text(row: dict) -> str:
    if row.get("by_design"):
        return f"by design; held out for testing: {row['by_design']}"
    if row["verdict"] == "PRESENT":
        parts = []
        if row.get("samples"):
            parts.append(f"{row['samples']} sample(s)")
        if row.get("cells") is not None:
            parts.append(f"{row['cells']:,} cells")
        return " · ".join(parts) or ("the study is listed" if row.get("overlap_level") == "study" else "—")
    if row.get("keys_absent"):
        none = "none exists: " + ", ".join(k.upper() if k in ("pmid", "doi") else k for k in row["keys_absent"])
        return f"{row['reason']} · {none}" if row.get("reason") else none
    return row.get("reason") or "—"


_RELATIVE_DOC_LINK_RE = re.compile(r"\]\((?!https?:|#|/)([^)\s]+\.md)(#[^)\s]*)?\)")


def _absolute_doc_links(text: str, repo_url: str) -> str:
    """Point relative links between docs/ files at the repo, since only DATA_FLOW is rendered here."""
    return _RELATIVE_DOC_LINK_RE.sub(lambda m: f"]({repo_url}/blob/main/docs/{m.group(1)}{m.group(2) or ''})", text)


def _github_slugify(value: str, separator: str) -> str:
    """GitHub's own heading-anchor algorithm: lowercase; drop anything not word/space/hyphen;
    replace EACH remaining whitespace character with `separator` (never collapsed).

    "3 — Looking up a dataset" -> the em dash is dropped, leaving the two spaces around it
    adjacent -> "3--looking-up-a-dataset" (a double separator), matching the anchor
    about.html#3--looking-up-a-dataset links against.
    """
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"\s", separator, value)


def _render_markdown_with_mermaid(text: str) -> str:
    """Markdown -> HTML, except ```mermaid fences become `<pre class="mermaid">RAW TEXT</pre>`.

    The raw diagram source is HTML-escaped, not code-rendered, so mermaid.js's `textContent`
    read recovers the exact original text (entities round-trip through the DOM) and the diagram
    stays readable as plain text if the script never loads. Headings get stable, GitHub-style
    ids (via the `toc` extension) so a page can link to one directly, e.g. #3--looking-up-a-dataset.
    """
    extensions = ["tables", "fenced_code", "toc"]
    extension_configs = {"toc": {"slugify": _github_slugify}}
    pieces: list[str] = []
    pos = 0
    for m in _MERMAID_FENCE_RE.finditer(text):
        before = text[pos:m.start()]
        if before.strip():
            pieces.append(_markdown.markdown(before, extensions=extensions, extension_configs=extension_configs))
        pieces.append(f'<pre class="mermaid">{html.escape(m.group(1))}</pre>')
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        pieces.append(_markdown.markdown(tail, extensions=extensions, extension_configs=extension_configs))
    return "\n".join(pieces)


def _environment() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["verdictslug"] = lambda v: v.lower().replace(" ", "-")
    return env


# --------------------------------------------------------------------------------- page context builders

def _model_order(models: dict) -> list[str]:
    return sorted(models, key=lambda mid: (models[mid]["model"].lower(), mid))


def _dataset_name(datasets: dict, did: str) -> str:
    return (datasets.get(did) or {}).get("name") or did


def _why_phrases(rows: list[dict], corpus: dict) -> list[str]:
    """The distinct reasons, in a few words each, that these INCONCLUSIVE / NOT CHECKABLE findings
    give. Ordered as _WHY lists them; no counts."""
    found: list[str] = []
    for test, phrase in _WHY:
        hits = [r for r in rows if test(r.get("reason") or "")]
        if not hits:
            continue
        if phrase == "unresolved":
            n = len(corpus.get("unresolved_entries") or ())
            phrase = (f"its list names {n} {'collection or source' if n == 1 else 'collections or sources'} "
                      "without their datasets, and a dataset missing from it could be inside one")
        elif len(hits) < len(rows):
            phrase = "for some, " + phrase
        found.append(phrase)
    return found


def _stage_summary(mid: str, corpus: dict, evals: list[str], by_key: dict, datasets: dict) -> dict:
    """One training stage in a sentence or two: the reported evals found in its corpus, by name,
    and the rest described in words, with why any are inconclusive. Shared by the home page and
    the top of each model page."""
    if corpus["manifest_type"] == "by-design":
        return {"by_design": True, "present": [], "rest": None, "why": []}
    rows = [by_key.get((mid, corpus["stage"], did)) for did in evals]
    present = sorted(({"dataset": r["dataset"], "name": _dataset_name(datasets, r["dataset"]),
                       "provisional": r.get("identity") == "provisional"}
                      for r in rows if r and r["verdict"] == "PRESENT"),
                     key=lambda x: (x["name"].lower(), x["dataset"]))
    rest_verdicts = [v for v in REST_LABEL if any((r["verdict"] if r else None) == v
                                                  for r in rows if not (r and r["verdict"] == "PRESENT"))]
    rest = None
    if rest_verdicts:
        words = " or ".join(REST_LABEL[v] for v in rest_verdicts)
        rest = (f"The other reported evals are {words}." if present else
                f"Its reported evals are {words}." if len(rest_verdicts) > 1 or len(rows) > 1 else
                f"Its reported eval is {words}.")
    unclear = [r for r in rows if r and r["verdict"] in ("INCONCLUSIVE", "NOT CHECKABLE")]
    return {"by_design": False, "present": present, "rest": rest, "why": _why_phrases(unclear, corpus)}


def _build_model_rows(ledger: dict) -> list[dict]:
    """Home page: one card per model. Each stage gets a summary (the reported evals found in its
    corpus, by name) and, folded, every reported eval in exactly one answer column.
    Alphabetical models; no counts, totals or ordering by verdict."""
    models, datasets = ledger["models"], ledger["datasets"]
    by_key = {(r["model"], r["stage"], r["dataset"]): r for r in ledger["rows"]}
    out = []
    for mid in _model_order(models):
        m = models[mid]
        evals = list(dict.fromkeys(e["dataset"] for e in m["evaluated_on"]))
        stages = []
        for c in m["corpora"]:
            cols: dict[str, list[dict]] = {"present": [], "not_present": [], "unclear": []}
            by_design_stage = c["manifest_type"] == "by-design"
            for did in evals:
                r = by_key.get((mid, c["stage"], did))
                if by_design_stage and not r:
                    continue   # a by-design stage is about the datasets it trains on part of, nothing else
                verdict = r["verdict"] if r else None
                provisional = bool(r and r.get("identity") == "provisional")
                by_design = bool(r and r.get("by_design"))
                title = verdict or "not checked"
                if provisional:
                    title += " · provisional identity"
                if by_design:
                    title = f"trained on by design; held out for testing: {r['by_design']}"
                cols[ANSWER_COLUMN.get(verdict, "unclear")].append({
                    "dataset": did,
                    "name": _dataset_name(datasets, did),
                    "verdict": verdict,
                    "suffix": ("by design" if by_design else
                               UNCLEAR_SUFFIX.get(verdict) if ANSWER_COLUMN.get(verdict, "unclear") == "unclear" else None),
                    "provisional": provisional,
                    "by_design": by_design,
                    "title": title,
                    "href": (f"models/{mid}.html#finding-{did}-{c['stage']}" if r else f"datasets/{did}.html"),
                })
            for chips in cols.values():
                chips.sort(key=lambda x: (x["name"].lower(), x["dataset"]))
            stages.append({"stage": c["stage"], "manifest_type": c["manifest_type"],
                           "manifest_short": MANIFEST_TYPE_SHORT.get(c["manifest_type"], ""), **cols,
                           "summary": _stage_summary(mid, c, evals, by_key, datasets)})
        out.append({"id": mid, "name": m["model"], "draft": m["draft"],
                    "task_specific": m.get("model_class") == "task-specific", "stages": stages})
    return out


def _group_model_rows(rows: list[dict]) -> list[dict]:
    """Home page sections: foundation models, then task-specific ones. Empty sections are left out."""
    out = []
    for cls, title, note in MODEL_GROUPS:
        members = [r for r in rows if r["task_specific"] == (cls == "task-specific")]
        if members:
            out.append({"key": cls, "title": title, "note": note, "models": members})
    return out


def _build_reuse_table(ledger: dict) -> list[dict]:
    """One row per dataset that is in one model's training corpus and another model's reported evals."""
    models, datasets = ledger["models"], ledger["datasets"]
    out = []
    for did in sorted(datasets, key=lambda did: (_dataset_name(datasets, did).lower(), did)):
        reuse = datasets[did].get("reuse") or ()
        if not reuse:
            continue
        trained = sorted({(x["in_training_of"], x["stage"]) for x in reuse},
                         key=lambda p: (models[p[0]]["model"].lower(), p[0], p[1]))
        evaluated = sorted({x["evaluated_by"] for x in reuse}, key=lambda mid: (models[mid]["model"].lower(), mid))
        out.append({
            "id": did, "name": _dataset_name(datasets, did),
            "trained": [{"id": mid, "name": models[mid]["model"], "draft": models[mid]["draft"], "stage": stage}
                        for mid, stage in trained],
            "evaluated": [{"id": mid, "name": models[mid]["model"], "draft": models[mid]["draft"]} for mid in evaluated],
        })
    return out


def _build_grid(ledger: dict) -> tuple[list[dict], list[dict]]:
    """"All datasets" page: datasets (grouped by kind) x models. Cells only ever hold findings for
    one pair; a reported eval is marked on its cell. No per-model or per-dataset total."""
    models, datasets = ledger["models"], ledger["datasets"]
    rows_by_pair: dict[tuple[str, str], list[dict]] = {}
    for r in ledger["rows"]:
        rows_by_pair.setdefault((r["dataset"], r["model"]), []).append(r)
    stage_order = {mid: [c["stage"] for c in models[mid]["corpora"]] for mid in models}

    grid_models = [{"id": mid, "name": models[mid]["model"], "draft": models[mid]["draft"],
                    "multi_stage": len(models[mid]["corpora"]) > 1} for mid in _model_order(models)]

    def kind_key(kind):
        if kind in KIND_ORDER:
            return (0, KIND_ORDER.index(kind), "")
        return (1, 0, kind) if kind else (2, 0, "")

    groups: dict[str | None, list[dict]] = {}
    for did in sorted(datasets, key=lambda did: (_dataset_name(datasets, did).lower(), did)):
        d = datasets[did]
        evaluated_by = set(d.get("evaluated_by") or ())
        cells = {}
        for gm in grid_models:
            pair = sorted(rows_by_pair.get((did, gm["id"]), ()),
                          key=lambda r: (stage_order[gm["id"]].index(r["stage"])
                                         if r["stage"] in stage_order[gm["id"]] else len(stage_order[gm["id"]]), r["stage"]))
            cells[gm["id"]] = {
                "reported_eval": gm["id"] in evaluated_by,
                "findings": [{"stage": r["stage"], "verdict": r["verdict"],
                              "short": "split" if r.get("by_design") else GRID_SHORT.get(r["verdict"], r["verdict"]),
                              "provisional": r.get("identity") == "provisional", "by_design": r.get("by_design")}
                             for r in pair],
            }
        groups.setdefault(d.get("kind"), []).append({"id": did, "name": _dataset_name(datasets, did), "cells": cells})

    grid_groups = [{"kind": k or "other", "label": (k or "other").capitalize(), "rows": groups[k]}
                   for k in sorted(groups, key=kind_key)]
    return grid_groups, grid_models


def _model_page_corpora(model_id: str, model: dict, ledger: dict) -> list[dict]:
    datasets = ledger["datasets"]
    rows_by_stage: dict[str, list[dict]] = {}
    for r in ledger["rows"]:
        if r["model"] == model_id:
            rows_by_stage.setdefault(r["stage"], []).append(r)

    def _finding(r: dict) -> dict:
        return {
            "dataset": r["dataset"],
            "dataset_name": datasets[r["dataset"]].get("name") or r["dataset"],
            "stage": r["stage"],
            "verdict": r["verdict"],
            "relation": r["relation"],
            "keys_attempted": r.get("keys_attempted") or [],
            "keys_absent": r.get("keys_absent") or [],
            "matched_on": r.get("matched_on") or [],
            "samples": r.get("samples"),
            "sample_ids": r.get("sample_ids") or [],
            "cells": r.get("cells"),
            "overlap_level": r.get("overlap_level"),
            "reason": r.get("reason"),
            "note": r.get("note"),
            "identity": r.get("identity"),
            "identity_basis": r.get("identity_basis"),
            "by_design": r.get("by_design"),
        }

    by_key = {(r["model"], r["stage"], r["dataset"]): r for r in ledger["rows"]}
    evals = list(dict.fromkeys(e["dataset"] for e in model["evaluated_on"]))
    corpora = []
    for c in model["corpora"]:
        stage_rows = rows_by_stage.get(c["stage"], [])
        reported_eval = sorted((_finding(r) for r in stage_rows if r["relation"] == "reported-eval"),
                                key=lambda f: f["dataset_name"].lower())
        catalog = sorted((_finding(r) for r in stage_rows if r["relation"] == "catalog"),
                          key=lambda f: f["dataset_name"].lower())
        splits = [{**s, "name": _dataset_name(datasets, s["dataset"])} for s in c.get("splits") or []]
        corpora.append({**c, "splits": splits, "reported_eval_findings": reported_eval, "catalog_findings": catalog,
                        "reported_groups": _group_findings(reported_eval), "catalog_groups": _group_findings(catalog),
                        "manifest_short": MANIFEST_TYPE_SHORT.get(c["manifest_type"], ""),
                        "summary": _stage_summary(model_id, c, evals, by_key, datasets)})
    return corpora


_GROUP_ORDER = {"PRESENT": 0, "by design": 1, "NOT PRESENT": 2, "INCONCLUSIVE": 3, "NOT CHECKABLE": 4}


def _has_detail(f: dict) -> bool:
    """Whether a finding says anything of its own beyond what its group already says."""
    return bool(f["own_keys"] or f["keys_absent"] or f["matched_on"] or f["samples"] is not None
                or f["cells"] is not None or f["sample_ids"] or f["by_design"] or f["note"]
                or (f["overlap_level"] and f["verdict"] == "PRESENT"))


def _group_findings(findings: list[dict]) -> list[dict]:
    """Findings that share a verdict and a reason, shown once with their datasets listed under them.
    The keys attempted go on the group when every member shares them, else on each member. Each
    dataset keeps its own anchor and anything it says of its own."""
    groups: dict[tuple, dict] = {}
    for f in findings:
        key = (f["verdict"], bool(f["by_design"]), f["reason"] or "")
        groups.setdefault(key, {"verdict": f["verdict"], "by_design": bool(f["by_design"]),
                                "reason": f["reason"], "members": []})["members"].append(f)
    out = list(groups.values())
    for g in out:
        shared = len({tuple(f["keys_attempted"]) for f in g["members"]}) == 1
        g["keys_attempted"] = g["members"][0]["keys_attempted"] if shared else []
        g["members"] = [{**f, "own_keys": [] if shared else f["keys_attempted"]} for f in g["members"]]
        for f in g["members"]:
            f["has_detail"] = _has_detail(f)
        g["detailed"] = any(f["has_detail"] for f in g["members"])
    out.sort(key=lambda g: (_GROUP_ORDER["by design" if g["by_design"] else g["verdict"]],
                            g["members"][0]["dataset_name"].lower()))
    return out


def _how_checked(model: dict, ledger: dict) -> dict:
    """The three lists on a model page: what the tool checks, what a person reviewed, what isn't checked.

    Every line must be true of THIS entry, so each is derived from its sources and findings.
    """
    datasets = ledger["datasets"]
    tool: list[str] = []
    sources = [s for c in model["corpora"] for s in c.get("sources") or []]
    methods = [((s.get("confirmation") or {}).get("method")) for s in sources]
    if (n := methods.count("url_hash")):
        tool.append(f"{'Its manifest file matches' if n == 1 else f'Its {n} manifest files match'} the publisher's "
                    "download (SHA-256), and the downloads are re-checked weekly.")
    if (n := methods.count("api_snapshot")):
        tool.append(f"{'Its corpus snapshot is' if n == 1 else f'Its {n} corpus snapshots are'} a re-runnable export "
                    "of the corpus itself, fixed by SHA-256.")
    rerun = [c for c in model["corpora"] if c.get("sources") and all(s.get("committed") for s in c["sources"])]
    if rerun:
        stages = ", ".join(c["stage"] for c in rerun)
        tool.append(f"Every finding is re-run from the committed manifest on every change, and must match ({stages}).")
    if (others := [c["stage"] for c in model["corpora"] if c.get("sources") and c not in rerun]):
        tool.append(f"Findings for {', '.join(others)} can be re-run only with the publisher's files, which aren't "
                    "stored here.")
    tool.append("Every registry rule passes on every change (docs/RULES.md).")
    tool.append("Dataset identifiers are confirmed against NCBI or the dataset's own archive; any that aren't "
                "are marked provisional.")

    points = review_points(ledger, model["id"])

    checked = set(model.get("review_checked") or ())
    # One row per review check, in REVIEW_CHECKS order. A check is done only when the review recorded
    # it; a reviewed entry with no scope recorded can't say which were skipped, so none is marked.
    checks = [{"key": k, "label": CHECK_LABEL.get(k, k), "text": text, "points": points.get(k, []),
               "done": k in checked and not model.get("draft")}
              for k, text in REVIEW_CHECKS.items()]
    outstanding = []
    if unchecked := model.get("discovery_unchecked"):
        outstanding.append("Not yet searched for a description of the training data: "
                           + ", ".join(DISCOVERY_LABEL.get(k, k) for k in unchecked) + ".")
    return {"tool": tool, "checks": checks, "outstanding": outstanding, "scope_recorded": bool(checked)}


def _dataset_page_context(dataset_id: str, dataset: dict, ledger: dict) -> dict:
    models = ledger["models"]

    def _row(r: dict) -> dict:
        mod = models[r["model"]]
        return {"model_id": r["model"], "model_name": mod["model"], "model_draft": mod["draft"],
                "stage": r["stage"], "relation": r["relation"], "verdict": r["verdict"],
                "evidence": _evidence_text(r),
                "identity": r.get("identity"), "identity_basis": r.get("identity_basis")}

    rows = sorted((_row(r) for r in ledger["rows"] if r["dataset"] == dataset_id),
                   key=lambda x: (x["model_name"].lower(), x["stage"]))

    evaluated_by_models = [
        {"id": mid, "name": models[mid]["model"], "draft": models[mid]["draft"]}
        for mid in sorted(dataset.get("evaluated_by") or (), key=lambda mid: models[mid]["model"].lower())
    ]

    reuse = [
        {"train_id": x["in_training_of"], "train_name": models[x["in_training_of"]]["model"],
         "stage": x["stage"], "eval_id": x["evaluated_by"], "eval_name": models[x["evaluated_by"]]["model"]}
        for x in sorted(dataset.get("reuse") or (),
                         key=lambda x: (models[x["in_training_of"]]["model"].lower(),
                                        models[x["evaluated_by"]]["model"].lower()))
    ]

    identifiers = [{**ident, "url": _identifier_url(ident["type"], ident["value"])}
                   for ident in dataset.get("identifiers") or ()]

    return {
        "id": dataset_id, "name": dataset.get("name") or dataset_id, "kind": dataset.get("kind"),
        "organism": dataset.get("organism") or [], "note": dataset.get("note"),
        "identifiers": identifiers, "sources": dataset.get("sources") or [],
        "evaluated_by_models": evaluated_by_models, "rows": rows, "reuse": reuse,
    }


# --------------------------------------------------------------------------------- entry point

def build_site(root: Path, out: Path, repo_url: str) -> list[Path]:
    """Build the static site into `out`. Returns every file written, as absolute paths, sorted.

    Deterministic: `out` is wiped and rebuilt from scratch each call, every iteration over the
    ledger is explicitly sorted, and no wall-clock timestamp is written anywhere — building twice
    produces byte-identical files.
    """
    root = Path(root)
    out = Path(out)
    repo_url = (repo_url or PLACEHOLDER_REPO_URL).rstrip("/")

    ledger = build_ledger(root)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "models").mkdir()
    (out / "datasets").mkdir()

    env = _environment()
    written: list[Path] = []

    def _write(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        written.append(path)

    common = dict(
        repo_url=repo_url, placeholder_repo_url=PLACEHOLDER_REPO_URL, exposure_note=EXPOSURE_NOTE,
        verdict_explanations=VERDICT_EXPLANATION, manifest_type_explanations=MANIFEST_TYPE_EXPLANATION,
        confirmation_explanations=CONFIRMATION_EXPLANATION, model_class_explanations=MODEL_CLASS_EXPLANATION,
    )

    # -- index.html: reported evals by model -------------------------------------------
    _write(out / "index.html", env.get_template("index.html").render(
        **common, root_prefix="", page_kind="index",
        correction_url=_correction_url(repo_url, "site index"), source_url=None,
        intro_paragraph=INTRO_PARAGRAPH,
        model_groups=_group_model_rows(_build_model_rows(ledger)), reuse_rows=_build_reuse_table(ledger),
    ))

    # -- all-datasets.html: the full datasets x models grid ----------------------------------
    grid_groups, grid_models = _build_grid(ledger)
    _write(out / "all-datasets.html", env.get_template("grid.html").render(
        **common, root_prefix="", page_kind="grid",
        correction_url=_correction_url(repo_url, "all datasets"), source_url=None,
        grid_groups=grid_groups, grid_models=grid_models,
    ))

    # -- about.html -----------------------------------------------------------------------
    data_flow_text = (root / "docs" / "DATA_FLOW.md").read_text(encoding="utf-8")
    _write(out / "about.html", env.get_template("about.html").render(
        **common, root_prefix="", page_kind="about",
        correction_url=_correction_url(repo_url, "about page"), source_url=None,
        data_flow_html=_render_markdown_with_mermaid(_absolute_doc_links(data_flow_text, repo_url)),
        rules_url=_rules_url(repo_url),
    ))

    # -- models/<id>.html -------------------------------------------------------------------
    model_template = env.get_template("model.html")
    for model_id in sorted(ledger["models"]):
        model = ledger["models"][model_id]
        page_model = {**model, "id": model_id, "corpora": _model_page_corpora(model_id, model, ledger),
                      "how_checked": _how_checked({**model, "id": model_id}, ledger)}
        _write(out / "models" / f"{model_id}.html", model_template.render(
            **common, root_prefix="../", page_kind="model",
            correction_url=_correction_url(repo_url, model["model"]),
            source_url=_source_url(repo_url, "model", model_id),
            model=page_model,
        ))

    # -- datasets/<id>.html -----------------------------------------------------------------
    dataset_template = env.get_template("dataset.html")
    for dataset_id in sorted(ledger["datasets"]):
        dataset = ledger["datasets"][dataset_id]
        page_dataset = _dataset_page_context(dataset_id, dataset, ledger)
        _write(out / "datasets" / f"{dataset_id}.html", dataset_template.render(
            **common, root_prefix="../", page_kind="dataset",
            correction_url=_correction_url(repo_url, page_dataset["name"]),
            source_url=_source_url(repo_url, "dataset", dataset_id),
            dataset=page_dataset,
        ))

    # -- lookup.html + lookup-index.json ---------------------------------------------------
    models_for_filter = [
        {"id": mid, "name": ledger["models"][mid]["model"], "draft": ledger["models"][mid]["draft"]}
        for mid in sorted(ledger["models"], key=lambda mid: (ledger["models"][mid]["model"].lower(), mid))
    ]
    lookup_index = build_lookup_index(root, include_drafts=True)
    index_json = json.dumps(lookup_index, sort_keys=True, separators=(",", ":"))
    _write(out / "lookup-index.json", index_json)

    config_json = json.dumps({"indexUrl": "lookup-index.json", "repoUrl": repo_url, "rootPrefix": ""},
                             sort_keys=True, separators=(",", ":"))
    _write(out / "lookup.html", env.get_template("lookup.html").render(
        **common, root_prefix="", page_kind="lookup",
        correction_url=_correction_url(repo_url, "lookup page"), source_url=None,
        disclaimer=PAPER_LEVEL_DISCLAIMER, models=models_for_filter,
        add_dataset_url=_add_dataset_url(repo_url), config_json=config_json,
    ))

    # -- style.css / lookup-core.mjs / lookup.js: copied verbatim, never templated --------
    css = (_TEMPLATES_DIR / "style.css").read_text(encoding="utf-8")
    _write(out / "style.css", css)
    lookup_core_js = (_TEMPLATES_DIR / "lookup-core.mjs").read_text(encoding="utf-8")
    _write(out / "lookup-core.mjs", lookup_core_js)
    lookup_page_js = (_TEMPLATES_DIR / "lookup.js").read_text(encoding="utf-8")
    _write(out / "lookup.js", lookup_page_js)

    return sorted(written)
