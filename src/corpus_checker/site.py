"""Generates the static site from `build_ledger()`. DERIVED — never a data source.

Pages: `index.html` (the models x datasets matrix + reuse map), `models/<id>.html`,
`datasets/<id>.html`, `about.html` (docs/DATA_FLOW.md rendered) and one `style.css`.
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
import re
import shutil
from pathlib import Path
from urllib.parse import quote as _urlquote

import jinja2
import markdown as _markdown

from .ledger import build_ledger

# --------------------------------------------------------------------------------- constants

PLACEHOLDER_REPO_URL = "https://github.com/OWNER/corpus-checker"

EXPOSURE_NOTE = ("Exposure, not effect: overlap does not by itself mean a reported number is "
                  "inflated. NOT CHECKABLE is a finding about what was published, not an error.")

INTRO_PARAGRAPH = (
    "corpus-checker checks, for each single-cell foundation model in the registry, whether the "
    "datasets it is evaluated on also appear in that model's own published pretraining corpus. "
    "This is exposure, not effect: overlap does not by itself mean a reported number is inflated "
    "— it means the evaluation data was available to see during training, which is worth knowing "
    "regardless of impact. Every verdict below is for one model, one training stage and one "
    "dataset; there is no per-model score, grade or rank anywhere on this site."
)

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
}

CONFIRMATION_EXPLANATION = {
    "url_hash": "downloaded from the publisher or repo URL; the hash is of those bytes",
    "api_snapshot": "a deterministic, re-runnable export of a versioned corpus's own API",
    "totals": "the file's contents add up to counts printed in the paper",
    "author": "the authors confirmed it in writing",
    "none": "supplied, not yet confirmed",
}

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


def _source_url(repo_url: str, kind: str, id_: str) -> str:
    folder = "registry" if kind == "model" else "datasets"
    return f"{repo_url}/blob/main/{folder}/{id_}.yaml"


def _rules_url(repo_url: str) -> str:
    return f"{repo_url}/blob/main/docs/RULES.md"


def _evidence_text(row: dict) -> str:
    if row["verdict"] == "PRESENT":
        parts = []
        if row.get("samples"):
            parts.append(f"{row['samples']} sample(s)")
        if row.get("cells") is not None:
            parts.append(f"{row['cells']:,} cells")
        return " · ".join(parts) or "—"
    return row.get("reason") or "—"


_RELATIVE_DOC_LINK_RE = re.compile(r"\]\((?!https?:|#|/)([^)\s]+\.md)(#[^)\s]*)?\)")


def _absolute_doc_links(text: str, repo_url: str) -> str:
    """Point relative links between docs/ files at the repo, since only DATA_FLOW is rendered here."""
    return _RELATIVE_DOC_LINK_RE.sub(lambda m: f"]({repo_url}/blob/main/docs/{m.group(1)}{m.group(2) or ''})", text)


def _render_markdown_with_mermaid(text: str) -> str:
    """Markdown -> HTML, except ```mermaid fences become `<pre class="mermaid">RAW TEXT</pre>`.

    The raw diagram source is HTML-escaped, not code-rendered, so mermaid.js's `textContent`
    read recovers the exact original text (entities round-trip through the DOM) and the diagram
    stays readable as plain text if the script never loads.
    """
    pieces: list[str] = []
    pos = 0
    for m in _MERMAID_FENCE_RE.finditer(text):
        before = text[pos:m.start()]
        if before.strip():
            pieces.append(_markdown.markdown(before, extensions=["tables", "fenced_code"]))
        pieces.append(f'<pre class="mermaid">{html.escape(m.group(1))}</pre>')
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        pieces.append(_markdown.markdown(tail, extensions=["tables", "fenced_code"]))
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

def _build_matrix(ledger: dict) -> tuple[list[dict], list[dict], bool]:
    """Rows = datasets (alphabetical), columns = models (alphabetical). No per-model total."""
    rows_by_pair: dict[tuple[str, str], list[dict]] = {}
    stages_seen: set[str] = set()
    for r in ledger["rows"]:
        rows_by_pair.setdefault((r["dataset"], r["model"]), []).append(r)
        stages_seen.add(r["stage"])
    for pair_rows in rows_by_pair.values():
        pair_rows.sort(key=lambda r: r["stage"])

    models = ledger["models"]
    matrix_models = [
        {"id": mid, "name": models[mid]["model"], "draft": models[mid]["draft"]}
        for mid in sorted(models, key=lambda mid: (models[mid]["model"].lower(), mid))
    ]

    datasets = ledger["datasets"]
    matrix_rows = []
    for did in sorted(datasets, key=lambda did: ((datasets[did].get("name") or did).lower(), did)):
        d = datasets[did]
        cells = {}
        for model in matrix_models:
            pair_rows = rows_by_pair.get((did, model["id"]))
            if pair_rows:
                cells[model["id"]] = [{"stage": r["stage"], "verdict": r["verdict"], "relation": r["relation"]}
                                       for r in pair_rows]
        matrix_rows.append({"id": did, "name": d.get("name") or did, "cells": cells})

    return matrix_rows, matrix_models, len(stages_seen) > 1


def _reuse_sentences(ledger: dict) -> list[dict]:
    models = ledger["models"]
    datasets = ledger["datasets"]
    out = []
    for did in sorted(datasets, key=lambda did: ((datasets[did].get("name") or did).lower(), did)):
        d = datasets[did]
        reuse = sorted(d.get("reuse") or (),
                        key=lambda x: (models[x["in_training_of"]]["model"].lower(),
                                       models[x["evaluated_by"]]["model"].lower()))
        for x in reuse:
            out.append({
                "dataset_id": did, "dataset_name": d.get("name") or did,
                "train_id": x["in_training_of"], "train_name": models[x["in_training_of"]]["model"],
                "stage": x["stage"],
                "eval_id": x["evaluated_by"], "eval_name": models[x["evaluated_by"]]["model"],
            })
    return out


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
            "matched_on": r.get("matched_on") or [],
            "samples": r.get("samples"),
            "sample_ids": r.get("sample_ids") or [],
            "cells": r.get("cells"),
            "overlap_level": r.get("overlap_level"),
            "reason": r.get("reason"),
            "note": r.get("note"),
        }

    corpora = []
    for c in model["corpora"]:
        stage_rows = rows_by_stage.get(c["stage"], [])
        self_eval = sorted((_finding(r) for r in stage_rows if r["relation"] == "self-eval"),
                            key=lambda f: f["dataset_name"].lower())
        catalog = sorted((_finding(r) for r in stage_rows if r["relation"] == "catalog"),
                          key=lambda f: f["dataset_name"].lower())
        corpora.append({**c, "self_eval_findings": self_eval, "catalog_findings": catalog})
    return corpora


def _dataset_page_context(dataset_id: str, dataset: dict, ledger: dict) -> dict:
    models = ledger["models"]

    def _row(r: dict) -> dict:
        mod = models[r["model"]]
        return {"model_id": r["model"], "model_name": mod["model"], "model_draft": mod["draft"],
                "stage": r["stage"], "relation": r["relation"], "verdict": r["verdict"],
                "evidence": _evidence_text(r)}

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

    # -- index.html ---------------------------------------------------------------------
    matrix_rows, matrix_models, matrix_multi_stage = _build_matrix(ledger)
    _write(out / "index.html", env.get_template("index.html").render(
        **common, root_prefix="", page_kind="index",
        correction_url=_correction_url(repo_url, "site index"), source_url=None,
        intro_paragraph=INTRO_PARAGRAPH,
        matrix_rows=matrix_rows, matrix_models=matrix_models, matrix_multi_stage=matrix_multi_stage,
        reuse_sentences=_reuse_sentences(ledger),
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
        page_model = {**model, "id": model_id, "corpora": _model_page_corpora(model_id, model, ledger)}
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

    # -- style.css: copied verbatim, never templated --------------------------------------
    css = (_TEMPLATES_DIR / "style.css").read_text(encoding="utf-8")
    _write(out / "style.css", css)

    return sorted(written)
