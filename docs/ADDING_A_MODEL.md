# Adding a model to the registry

*The workflow, with the decision points and the places it actually goes wrong.*
*Rough cost: **1–3 hours** per model. Most of that is Phases 2 and 3 — the machine work is minutes.*

---

## Phase 1 — Triage *(semi-automated: Tier 1)*

**1. Get the paper.** PMID or DOI. If only a preprint exists, ⚠ **check for a published version first** — *Geneformer's `Nature` version has a different corpus, parameter count, input window **and vocabulary** than the preprint, and the repo default is now a model the original paper does not describe.*

**2. Pull Data availability + Code availability.** Free on the journal landing page even when the article is paywalled. *(Verified for Nature-family: scFoundation, scGPT, Geneformer.)*

**★★★★★ 2a. THEN ENUMERATE EVERY SUPPLEMENTARY FILE. The availability statement is not sufficient and must never be the only thing read.**
> ⚠ **Geneformer's data availability statement is one sentence and points only at the tokenized corpus.** It never mentions **Supplementary Table 1 — `Genecorpus-30M_composition`**, which is the complete 561-row manifest. *Classifying from the statement alone produced a `NOT CHECKABLE` verdict for a model that publishes the most detailed manifest in the set.*
>
> | Model | Where the manifest pointer lives |
> |---|---|
> | **scFoundation** | **inside** the availability statement — one clause in a long sentence |
> | ⚠ **Geneformer** | **nowhere in it.** Only in the supplementary file list |
>
> ⇒ **Two different failure modes, same wrong answer.** ★ **Open the supplementary file list and look at every table's title before concluding a manifest does not exist.** *A file called `*_composition` is a manifest whatever the availability statement says.*

**★ 2b. Record where you looked in the entry's `discovery` block — including the places you found nothing.** Nine locations: data availability · code availability · supplementary files *(list every title in `files`)* · methods · GitHub repo · HF model card · HF dataset card · Zenodo/figshare · preprint vs published. **`checked: true` means you looked there *for a training-data manifest*** — having browsed the repo for something else does not count. *CI (G11) refuses `NOT CHECKABLE` until all nine are checked.*

**3. Regex every accession.** `GSE\d+` · `GSM\d+` · `E-MTAB-\d+` · `SRP\d+` · `PRJNA\d+` · `10\.\d{4,}/\S+` · `figshare|zenodo|dataverse`

**4. ★ Classify the manifest type. This is the whole job.**

```
Does the paper point to a file listing its training data?
├─ YES, a supplementary table or repo file ................... TYPE A
├─ YES, and the corpus itself is downloadable ................ TYPE A+
├─ NO, but it names a versioned public corpus + release ...... TYPE B
├─ NO, but it names specific datasets in prose ............... TYPE C-named
└─ NO — only repository names, or nothing ................... TYPE C-vague / D
                                                              └─ STOP. Record NOT CHECKABLE.
                                                                 ★ This is a finding, not a failure.
```

> ⚠ **Read the whole availability statement before classifying.** scFoundation's manifest pointer is **one clause** inside a long sentence: *"…and the detailed dataset list we used is in Supplementary Data 1 and 2."* Miss it and the model looks like Type C-vague. **It is the single easiest error in this workflow.**

---

## Phase 2 — Locate and fetch *(human)*

**5. Follow the pointer.** Supplementary files on Nature-family journals are **free even when the article is not** — pattern: `media.springernature.com/original/springer-static/esm/art%3A{DOI}/MediaObjects/{JOURNAL}_{YEAR}_{ID}_MOESM{N}_ESM.{ext}`

**6. ⚠ Download XLSX manually.** *Fetch tools return binary. Expect to do this by hand.*

**7. Record the URL, an as-of date, and ★ `sha256` + `bytes`.** **Corpora get versioned and replaced** *(Genecorpus-30M → Genecorpus-104M; census releases are dated)* — **and a URL can serve different bytes later without telling you.**
> ★★ **The hash is what makes `as_of` mean anything.** A date says *when* we looked; the hash says *what we looked at*. ⇒ **Anyone re-downloading can confirm they hold the same file we scored** — and if they do not, the disagreement is located immediately instead of being mistaken for an error in the check.
> `sha256sum <file>` — for scFoundation: `786c6962…` (Data 1) and `681c1d88…` (Data 2).

**★ 7a. Record how you got the file and how you know it is the paper's.** `acquired: fetched` if you downloaded it from `url` yourself *(confirmation is then `url_hash` by construction)*. `acquired: supplied` + `supplied_by` if someone handed it to you — then pick the strongest `confirmation` you can honestly claim:

| method | when |
|---|---|
| `totals` | the file's contents add up to a number the paper prints — *Geneformer: Σ `Total_cells_passed` = 27,406,217, as in the Methods.* Put the number and where it is stated in `detail` |
| `author` | the authors confirmed it in writing — link the issue in `detail` |
| `none` | neither. **Honest, and allowed — but every verdict from this manifest is capped at `INCONCLUSIVE` (G09)** |

> ⚠ **An inferred URL is not a followed URL.** *Geneformer's `url` was built from the Springer pattern and never opened.* Leave a `note`; the weekly drift job (G04) will say when the URL serves the same bytes, and a verifier can then upgrade to `url_hash`.

---

## Phase 3 — Extract the evaluation datasets *(human — the under-appreciated half)*

**8. ★★★ Usually in the SAME availability statement as the training sources — sometimes the same paragraph.**
*scGPT: `"Pretraining datasets can be retrieved from the CELLxGENE census…"` then a few sentences later `"For the perturbation prediction task, the Norman and Adamson datasets were retrieved from…"`* ⚠ **Conflating the two halves is the failure mode here. Read for the verb: *pretrained on* vs *evaluated on*.**

**9. Resolve each to a canonical triple** — accession · DOI · PMID — **and find or add it in `datasets/`.** **Reuse `crosswalk/`; do not re-resolve by hand.** Mark an identifier `confirmed: true` only when it was resolved through the crosswalk or checked against a primary source — *an unconfirmed identifier never counts as an attempted key (G15)*.
> ⚠ **Resolve, don't assume.** *The original scFoundation script searched `GSE93421` for Zheng68K. NCBI says `GSE93421` is the same paper's 1.3M-cell **mouse brain** dataset.*
> ★ **The chain, verified working:**
> `GSE133344` → eUtils `esearch db=gds` → UID `200133344` → `esummary` → `pubmedids: ["31395745"]` → NCBI ID Converter → `10.1126/science.aax4438`
> ★★ `esummary` **also returns every sample accession with its title** — *"sgRNA perturb-seq experiment"* — which is a **better perturbation detector than grepping study titles** *(grepping "screen" pulled in a thalamic-development collection)*.

**10. Tag which task each dataset serves** in `evaluated_on`. *A model can be clean on one task and exposed on another.* These become the model's **reported-eval** findings; the rest of the catalog is checked too and shown as **catalog** findings.

**10a. One corpus per training stage.** If the model trains in stages with different data *(STATE: embedding pretraining, then perturbation training)*, each stage gets its own `corpora[]` entry with its own manifest and findings.

---

## Phase 4 — Resolve and check *(automated)*

**11. Normalise the manifest** to canonical records via the matching `resolvers/`.

**12. ⚠ Match on BOTH accession and PMID — never one.** *scFoundation's study table had **161 of 522 rows with a blank PMID**, and its sample table had **134 of 656 projects with no title or PMID at all**. Either key alone silently misses rows.*

**13. Apply the verdict rules** *(see [`RULES.md`](RULES.md))*. **Keyword-only hits are `INCONCLUSIVE`, never `PRESENT`.** **Type-B can return `NOT PRESENT`, never `PRESENT`.**

---

## Phase 5 — Record *(non-negotiable)*

**14. Write `registry/{id}.yaml`** — see `registry/scfoundation.yaml` for the worked example. **Every claim carries a source quote.** The `id` is the file name and becomes a URL; add a variant suffix when a model has more than one corpus *(`geneformer-30m`)*. Record `keys_attempted` **per finding**.

**14a. Run `corpus-checker validate --allow-draft`** until only G10 remains — that one is for the verifier.

**15. Add a regression test.** *There is already one asserting scFoundation still returns Norman with 8 samples / 125,081 cells. **Each new model gets its own.*** ⚠ *Manifests move; the test is how you find out.*

**16. Record who verified it and when.** The registry makes claims about other people's papers. **`verified_by` and `verified_date` are what make that defensible.**

---

## ⚠ The five errors made during the audit — all avoidable, all made anyway

| # | Error | Guard |
|---|---|---|
| 1 | Published `NOT PRESENT` from **one of two** manifest files | ★ **`sources_searched` is mandatory output** |
| 2 | Treated keyword hits as findings *("myeloid" → 20 unrelated)* | **separate verdict tier** |
| 3 | Missed the manifest pointer inside a long sentence | **read the whole statement before classifying** |
| 4 | Conflated pretraining sources with evaluation sources | **read for the verb** |
| 5 | Assumed a bare "not found" meant absent when only PMIDs were matched | **dual-key matching** |

---

## ★ What "done" looks like

A model is **added** when: the registry YAML exists with source quotes · the check runs from the CLI and reproduces by hand · a regression test passes · the verdict is one of the four, **with `sources_searched` populated** · and any `INCONCLUSIVE` row says *why*.

**A model with no manifest is still added** — as `NOT CHECKABLE`, with the availability statement quoted. ★ *Those entries are the point, not the residue: they are how the registry shows what the field does not publish.*
