"""The static site built from the ledger.

Builds once into a shared tmp_path (module-scoped) from the real registry/datasets plus one
synthetic draft entry (tests/conftest.py TOY_DRAFT), so draft labels, the reuse map and
INCONCLUSIVE reasons are exercised even when no real draft is in the registry. `tests/test_ledger.py`
already covers the ledger itself; this file covers what the HTML says and how it is structured.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from corpus_checker.ledger import build_ledger
from corpus_checker.lookup import build_lookup_index
from corpus_checker.site import build_site
from corpus_checker.verdict import PAPER_LEVEL_DISCLAIMER

from conftest import REPO, repo_copy

FAKE_REPO_URL = "https://github.com/example-org/corpus-checker"



@pytest.fixture(scope="module")
def root(tmp_path_factory) -> Path:
    return repo_copy(tmp_path_factory.mktemp("repo"))


@pytest.fixture(scope="module")
def ledger(root) -> dict:
    return build_ledger(root)


@pytest.fixture(scope="module")
def site_dir(tmp_path_factory, root) -> Path:
    out = tmp_path_factory.mktemp("site") / "_build"
    build_site(root, out, FAKE_REPO_URL)
    return out


def _read(site_dir: Path, rel: str) -> str:
    return (site_dir / rel).read_text(encoding="utf-8")


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# Block-level tags introduce a word break when stripped (so "</p><p>" doesn't glue two
# paragraphs' words together); inline tags like <a>/<span>/<strong> do not, so
# "...scFoundation</a>'s..." reads back as "scFoundation's", not "scFoundation 's".
_BLOCK_TAGS = {"p", "li", "tr", "td", "th", "div", "section", "article", "ul", "ol", "table",
               "h1", "h2", "h3", "h4", "h5", "h6", "dl", "dt", "dd", "header", "footer", "nav",
               "main", "thead", "tbody", "br", "blockquote", "pre", "caption", "hr", "html", "body"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        self.parts.append(data)


def _visible_text(raw_html: str) -> str:
    """The way a screen reader or a plain-text copy-paste would see the page — used wherever a
    test checks for a specific sentence rather than exact markup."""
    no_scripts = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    extractor = _TextExtractor()
    extractor.feed(no_scripts)
    return _normalize_ws("".join(extractor.parts))


# --------------------------------------------------------------------------------- pages exist

def test_index_and_about_exist(site_dir):
    assert (site_dir / "index.html").is_file()
    assert (site_dir / "about.html").is_file()
    assert (site_dir / "style.css").is_file()


def test_every_model_has_a_page(site_dir, ledger):
    for model_id in ledger["models"]:
        assert (site_dir / "models" / f"{model_id}.html").is_file(), model_id


def test_every_dataset_has_a_page(site_dir, ledger):
    for dataset_id in ledger["datasets"]:
        assert (site_dir / "datasets" / f"{dataset_id}.html").is_file(), dataset_id


# --------------------------------------------------------------------------------- content rules

def test_norman_reuse_sentence_on_index_and_dataset_page(site_dir):
    sentence = "Norman 2019 is in scFoundation's pretraining corpus, and Toy Draft evaluates on it."
    assert sentence in _visible_text(_read(site_dir, "index.html"))
    assert sentence in _visible_text(_read(site_dir, "datasets/norman2019.html"))


def test_draft_label_on_draft_model_pages(site_dir):
    for model_id in ("toy-draft",):
        text = _visible_text(_read(site_dir, f"models/{model_id}.html"))
        assert "Draft — not yet verified" in text
    # and NOT on the verified scFoundation entry
    assert "Draft — not yet verified" not in _visible_text(_read(site_dir, "models/scfoundation.html"))


def test_draft_label_follows_model_everywhere_it_appears(site_dir):
    # The toy draft appears on the index matrix, its own page, and every dataset page
    # that lists it — the label must travel with it, not just live on its own page.
    assert "Draft — not yet verified" in _visible_text(_read(site_dir, "index.html"))
    assert "Draft — not yet verified" in _visible_text(_read(site_dir, "datasets/norman2019.html"))


def test_correction_link_present_on_every_page(site_dir):
    for f in sorted(site_dir.rglob("*.html")):
        text = f.read_text(encoding="utf-8")
        assert "template=correction.yml" in text, f
        assert FAKE_REPO_URL in text, f


def test_model_and_dataset_pages_link_source_yaml(site_dir):
    scf = _read(site_dir, "models/scfoundation.html")
    assert f"{FAKE_REPO_URL}/blob/main/registry/scfoundation.yaml" in scf
    norman = _read(site_dir, "datasets/norman2019.html")
    assert f"{FAKE_REPO_URL}/blob/main/datasets/norman2019.yaml" in norman


def test_evidence_is_visible_on_model_page(site_dir):
    """Rule 5: verbatim quote, sha256 (shortened + full on hover), confirmation method,
    sources_searched, keys_attempted/matched_on, sample counts/cells."""
    scf = _read(site_dir, "models/scfoundation.html")
    assert "The pretraining datasets were mainly downloaded from GEO" in scf  # the verbatim quote
    assert 'title="786c6962d01bbeeffcc88335d2e2e762f333daf399c649bec3916bc4ef2209d0"' in scf  # full sha256, on hover
    assert "786c6962d01b" in scf  # shortened sha256 (first 12 hex chars) visible in the text
    assert "downloaded from the publisher or repo URL" in scf  # url_hash explained in plain words
    assert "Sources searched:" in scf
    assert "GSM3906020" in scf  # sample_ids
    assert "125,081 cells" in scf


def test_inconclusive_reason_always_shown(site_dir, ledger):
    inconclusive_rows = [r for r in ledger["rows"] if r["verdict"] == "INCONCLUSIVE"]
    assert inconclusive_rows  # sanity: the toy draft's census stage contributes one
    for row in inconclusive_rows:
        assert row.get("reason"), row  # schema requires it; assert the data actually has it too
        text = _visible_text(_read(site_dir, f"models/{row['model']}.html"))
        assert _normalize_ws(row["reason"]) in text, (row["model"], row["dataset"])


def test_not_checkable_is_not_styled_as_a_failure(site_dir):
    """It must use the same neutral chip markup as every other verdict, not an error class."""
    css = _read(site_dir, "style.css")
    assert "v-not-checkable" in css
    for bad in ("error", "fail", "danger", "invalid"):
        assert bad not in css.lower()


# --------------------------------------------------------------------------------- forbidden words (rule 1)

_ABSOLUTE_FORBIDDEN = ("contaminat", "leak", "cheat", "dirty")

# The one permitted phrasing (rule 1). It appears verbatim in EXPOSURE_NOTE, INTRO_PARAGRAPH and
# (after this task's edit) docs/DATA_FLOW.md's own hedge sentence — removing this fragment
# wherever it occurs covers all of those at once, regardless of the sentence around it.
_CANONICAL_HEDGE_FRAGMENT = "does not by itself mean a reported number is inflated"


def _allowed_inflated_snippets(ledger) -> list[str]:
    """Every other place 'inflated' may legitimately appear: the verbatim registry text this
    site is required to display as-is (quotes/caveats/notes/reasons) rather than rewrite, even
    where a verifier's own phrasing of the hedge differs from the canonical sentence above.
    Nothing outside the canonical fragment and these may use the word."""
    snippets = []
    for model in ledger["models"].values():
        note = (model.get("paper") or {}).get("note")
        if note:
            snippets.append(note)
        for corpus in model["corpora"]:
            snippets.extend(corpus.get("caveats") or [])
            if corpus.get("quote"):
                snippets.append(corpus["quote"])
    for dataset in ledger["datasets"].values():
        if dataset.get("note"):
            snippets.append(dataset["note"])
    for row in ledger["rows"]:
        if row.get("reason"):
            snippets.append(row["reason"])
        if row.get("note"):
            snippets.append(row["note"])
    return [_normalize_ws(s) for s in snippets if s]


def test_forbidden_words_absent_everywhere(site_dir, ledger):
    allowed = _allowed_inflated_snippets(ledger)
    for f in sorted(site_dir.rglob("*.html")):
        text = _visible_text(f.read_text(encoding="utf-8"))
        low = text.lower()
        for word in _ABSOLUTE_FORBIDDEN:
            assert word not in low, f"{f}: forbidden word root {word!r} found"
        assert not re.search(r"\bclean\b", low), f"{f}: 'clean' used as if it were a verdict"

        remainder = text.replace(_CANONICAL_HEDGE_FRAGMENT, "")
        for snippet in allowed:
            remainder = remainder.replace(snippet, "")
        assert "inflated" not in remainder.lower(), (
            f"{f}: an 'inflated' occurrence that is neither the canonical hedge sentence nor "
            "verbatim registry text"
        )


def test_canonical_hedge_sentence_is_exact(site_dir):
    assert "does not by itself mean a reported number is inflated" in _read(site_dir, "index.html")


# --------------------------------------------------------------------------------- structure (rule 2)

def test_matrix_has_no_per_model_total_or_score_column(site_dir, ledger):
    index_html = _read(site_dir, "index.html")
    table_match = re.search(r'<table class="matrix">.*?</table>', index_html, re.DOTALL)
    assert table_match, "no matrix table found"
    matrix_html = table_match.group(0)

    n_models = len(ledger["models"])
    thead = re.search(r"<thead>.*?</thead>", matrix_html, re.DOTALL).group(0)
    assert len(re.findall(r"<th\b", thead)) == n_models + 1  # "Dataset" + one per model, nothing more

    tbody = re.search(r"<tbody>.*?</tbody>", matrix_html, re.DOTALL).group(0)
    for row in re.findall(r"<tr>.*?</tr>", tbody, re.DOTALL):
        assert len(re.findall(r"<td\b", row)) == n_models  # no extra trailing "total" cell

    matrix_text = _visible_text(matrix_html).lower()
    for forbidden in ("total", "overall score", "overall rank", "grade", "average", "sort by"):
        assert forbidden not in matrix_text


def test_no_model_level_score_key_on_any_model_page(site_dir, ledger):
    forbidden = {"score", "grade", "rank", "overall"}
    for model_id in ledger["models"]:
        text = _visible_text(_read(site_dir, f"models/{model_id}.html")).lower()
        words = set(re.findall(r"[a-z]+", text))
        assert not (forbidden & words), model_id


# --------------------------------------------------------------------------------- links resolve

_EXTERNAL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://|^mailto:")


def _internal_hrefs(raw_html: str) -> list[str]:
    return [h for h in re.findall(r'href="([^"]*)"', raw_html)
            if h and not h.startswith("#") and not _EXTERNAL_SCHEME_RE.match(h)]


def test_every_internal_href_resolves_to_a_generated_file(site_dir):
    all_files = {str(p.relative_to(site_dir)) for p in site_dir.rglob("*") if p.is_file()}
    checked = 0
    for f in sorted(site_dir.rglob("*.html")):
        raw = f.read_text(encoding="utf-8")
        for href in _internal_hrefs(raw):
            path_part, _, fragment = href.partition("#")
            target = (f.parent / path_part).resolve()
            rel = target.relative_to(site_dir.resolve())
            assert str(rel) in all_files, f"{f} -> {href} (resolved {rel}, not built)"
            if fragment:
                target_text = target.read_text(encoding="utf-8")
                assert f'id="{fragment}"' in target_text, f"{f} -> {href}: anchor #{fragment} missing in {rel}"
            checked += 1
    assert checked > 20  # sanity: we actually exercised a meaningful number of links


# --------------------------------------------------------------------------------- determinism

def test_build_is_byte_identical_across_two_runs(tmp_path):
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    build_site(REPO, out_a, FAKE_REPO_URL)
    build_site(REPO, out_b, FAKE_REPO_URL)

    files_a = sorted(p.relative_to(out_a) for p in out_a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(out_b) for p in out_b.rglob("*") if p.is_file())
    assert files_a == files_b
    for rel in files_a:
        assert (out_a / rel).read_bytes() == (out_b / rel).read_bytes(), rel


# --------------------------------------------------------------------------------- placeholder repo url

def test_placeholder_repo_url_is_visible_when_unconfigured(tmp_path):
    from corpus_checker.site import PLACEHOLDER_REPO_URL

    out = tmp_path / "site"
    build_site(REPO, out, PLACEHOLDER_REPO_URL)
    text = (out / "index.html").read_text(encoding="utf-8")
    assert PLACEHOLDER_REPO_URL in text
    assert "placeholder" in _visible_text(text).lower()


# --------------------------------------------------------------------------------- about page

def test_about_page_renders_data_flow_and_mermaid_is_readable_as_text(site_dir):
    about = _read(site_dir, "about.html")
    assert "How data flows through corpus-checker" in about
    assert 'class="mermaid"' in about
    assert "mermaid@11" in about
    # the raw diagram source stays as readable text inside the <pre> even if the script never runs
    assert "flowchart TD" in about
    assert "Paper + supplements" in about
    assert "How to read a verdict" in about
    for verdict in ("PRESENT", "NOT PRESENT", "INCONCLUSIVE", "NOT CHECKABLE"):
        assert verdict in about


def test_about_page_has_the_lookup_section_anchor(site_dir):
    """docs/DATA_FLOW.md's '## 3 — Looking up a dataset' must get a stable, GitHub-style id so
    lookup.html's link to about.html#3--looking-up-a-dataset actually lands on the section."""
    about = _read(site_dir, "about.html")
    assert 'id="3--looking-up-a-dataset"' in about
    assert "Looking up a dataset" in about


# --------------------------------------------------------------------------------- dataset lookup page

def test_lookup_page_and_index_exist(site_dir):
    assert (site_dir / "lookup.html").is_file()
    assert (site_dir / "lookup-index.json").is_file()
    assert (site_dir / "lookup-core.mjs").is_file()
    assert (site_dir / "lookup.js").is_file()


def test_lookup_index_content_equals_build_lookup_index(site_dir, root):
    on_disk = json.loads(_read(site_dir, "lookup-index.json"))
    expected = build_lookup_index(root, include_drafts=True)
    assert on_disk == expected
    # every registered model (including drafts) is in the index — the page searches drafts too,
    # labelled as drafts, same as the rest of the site (only production builds contain none).
    assert {c["model"] for c in on_disk["corpora"]} == {"scfoundation", "toy-draft"}


def test_lookup_index_is_serialized_deterministically(site_dir):
    raw = (site_dir / "lookup-index.json").read_text(encoding="utf-8")
    assert "\n" not in raw  # one compact line: sort_keys + separators=(",", ":"), no whitespace padding
    assert '"index_version":1' in raw


def test_lookup_disclaimer_is_static_and_verbatim(site_dir):
    """The standing disclaimer under the input must be in the HTML before any JS runs, and must
    be the real PAPER_LEVEL_DISCLAIMER text, not a retyped paraphrase."""
    lookup_html = _read(site_dir, "lookup.html")
    assert PAPER_LEVEL_DISCLAIMER in lookup_html
    assert 'id="paper-level-disclaimer"' in lookup_html
    # it sits in a plain paragraph before the page's <script> tags, not inside one of them
    first_script_pos = lookup_html.index("<script")
    disclaimer_pos = lookup_html.index(PAPER_LEVEL_DISCLAIMER)
    assert disclaimer_pos < first_script_pos


def test_lookup_page_has_model_filter_checkboxes(site_dir):
    lookup_html = _read(site_dir, "lookup.html")
    for model_id in ("scfoundation", "toy-draft"):
        assert f'value="{model_id}"' in lookup_html
    # the draft must carry the same visible label as everywhere else
    assert "Draft — not yet verified" in lookup_html


def test_lookup_page_links_about_section_correction_and_add_dataset(site_dir):
    lookup_html = _read(site_dir, "lookup.html")
    assert 'href="about.html#3--looking-up-a-dataset"' in lookup_html
    assert "template=correction.yml" in lookup_html
    assert FAKE_REPO_URL in lookup_html

    m = re.search(r'id="add-dataset-link" href="([^"]+)"', lookup_html)
    assert m, "add-dataset link not found"
    url = html_lib_unescape(m.group(1))
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}".startswith(FAKE_REPO_URL)
    assert parts.path.endswith("/issues/new")
    qs = parse_qs(parts.query)
    assert qs.get("template") == ["add-dataset.yml"]
    assert qs.get("title", [""])[0].startswith("[add-dataset]")


def html_lib_unescape(s: str) -> str:
    import html as _html
    return _html.unescape(s)


def test_index_page_has_a_search_box_to_lookup(site_dir):
    index_html = _read(site_dir, "index.html")
    assert '<form action="lookup.html" method="get"' in index_html
    assert 'name="q"' in index_html


def test_lookup_page_present_on_every_page_nav(site_dir):
    for f in sorted(site_dir.rglob("*.html")):
        text = f.read_text(encoding="utf-8")
        assert "lookup.html" in text, f
