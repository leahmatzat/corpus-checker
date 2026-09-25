# corpus-checker

**Is a model's evaluation data inside its training corpus?**

A deterministic check that uses only what a paper published: no model access, no GPU, no scraping. **A check a reviewer can run on a laptop in ten minutes.**

It is built for people comparing single-cell foundation models. For each benchmark dataset, the registry records which models had it in their training corpus and which models evaluate on it. The lookup answers the same question for any dataset you're planning to evaluate on: *which models used this in training?*

## Status

Early. The registry schema, validator, resolvers and ledger are built and tested. scFoundation is verified. Entries for scGPT and Geneformer are in progress and will be added once they are verified. The public site will be at [huggingface.co/spaces/lmatzat/corpus-checker](https://huggingface.co/spaces/lmatzat/corpus-checker); it is rebuilt from this repository on every merge.

| | |
|---|---|
| [`docs/RULES.md`](docs/RULES.md) | **the four verdicts, the rules that limit them, the guard codes, and how to correct or dispute an entry** |
| [`docs/DATA_FLOW.md`](docs/DATA_FLOW.md) | how a verdict is produced, and how a change reaches the site (two diagrams) |
| [`docs/ADDING_A_MODEL.md`](docs/ADDING_A_MODEL.md) | the workflow for adding a model, including the mistakes it is designed to prevent |
| [`docs/VERIFYING.md`](docs/VERIFYING.md) | how a verifier reviews an entry and signs it off, and what to do about each validator message |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | publishing the static site to a Hugging Face Space |
| [`registry/`](registry/) | one YAML file per model; every claim carries a source. [`scfoundation.yaml`](registry/scfoundation.yaml) is the worked example |
| [`datasets/`](datasets/) | the shared benchmark catalog every model is checked against |
| [`schema/`](schema/) | machine-readable schemas for both |

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,xlsx]'
.venv/bin/corpus-checker validate                 # schema + guards over registry/ and datasets/
.venv/bin/corpus-checker check scfoundation       # a model's findings, as recorded
.venv/bin/corpus-checker check scfoundation --rerun  # re-run from the committed manifest extracts
.venv/bin/corpus-checker ledger                   # every dataset × model: who trained on it, who evaluates on it
.venv/bin/corpus-checker lookup GSE133344         # which models trained on this dataset? (accession, PMID, DOI, URL)
.venv/bin/pytest                                  # every guard, made to fire
```

Exit codes never encode verdicts: `0` means it ran, `1` a tool error, `2` a registry rule was violated and `3` bad usage.

## The first result

Norman et al. 2019 (GEO GSE133344) appears in scFoundation's 55M-cell pretraining corpus: 8 samples, 125,081 cells. Norman is also one of scFoundation's own 2-gene perturbation evaluation datasets, and scGPT evaluates on it too.

Everything needed to find this was public. The corpus manifest is free supplementary material, and the evaluation datasets are named in the same availability statement.

## What a result means

**Exposure, not effect.** Geneformer's own experiment fine-tuned on cells either inside or outside its pretraining corpus and found *"essentially equivalent results."* Overlap does not by itself mean a number is inflated.

**A `NOT CHECKABLE` verdict is a finding, not an error.** It means the paper did not publish enough to answer the question.

## Corrections

Open an issue using the *Correction* or *Dispute* form, or send a pull request. See [`docs/RULES.md`](docs/RULES.md#corrections).

## License

**Code: [MIT](LICENSE). Registry and dataset data: [CC0 1.0](registry/LICENSE).**

The registry is only useful if people can embed it without asking, so this is the most permissive pair available. Attribution is requested, not required.

## Related work

[`scContam`](https://arxiv.org/html/2607.20572) detects contamination statistically, using MinHash fingerprints and membership-inference attacks. It is more powerful and works without a published manifest, but it needs model access and compute. **This tool is the complement: weaker, deterministic, and runnable by anyone with a browser.**
