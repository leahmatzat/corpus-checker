# corpus-checker

**Is a model's evaluation data inside its training corpus?**

A deterministic check that uses only what a paper published: no model access, no GPU, no scraping.

## Status

Early. One model is verified (scFoundation) and one is a draft (Geneformer). Tooling is on the way.

| | |
|---|---|
| [`docs/ADDING_A_MODEL.md`](docs/ADDING_A_MODEL.md) | the workflow for adding a model |
| [`registry/scfoundation.yaml`](registry/scfoundation.yaml) | worked example; every claim carries a source quote |
| [`schema/registry.schema.json`](schema/registry.schema.json) | machine-readable entry schema |

## The first result

Norman et al. 2019 (GEO GSE133344) appears in scFoundation's 55M-cell pretraining corpus: 8 samples, 125,081 cells. Norman is one of scFoundation's own 2-gene perturbation evaluation datasets.

## What a result means

**Exposure, not effect.** Geneformer's own experiment fine-tuned on cells either inside or outside its pretraining corpus and found *"essentially equivalent results."* Overlap does not by itself mean a number is inflated.

**A `NOT CHECKABLE` verdict is a finding, not an error.** It means the paper did not publish enough to answer the question.

## License

**Code: [MIT](LICENSE). Registry data: [CC0 1.0](registry/LICENSE).**
