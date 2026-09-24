# Rules

How corpus-checker decides what it can claim, how those rules are enforced, and how to correct or dispute an entry.

**Every verdict is about one model, one training stage and one dataset.** There is no score, grade or ranking for a model as a whole.

**Exposure, not effect.** A dataset appearing in a model's training corpus does not by itself mean a reported number is inflated. The registry records what was published. It does not judge the work.

---

## The four verdicts

| Verdict | Means | Must carry |
|---|---|---|
| `PRESENT` | The dataset's own accession was found in the training-data manifest (a PMID or DOI alone identifies only the paper) | The keys that matched, the match level (dataset), the overlap level (study, sample or cell), and the matched samples or cell count |
| `NOT PRESENT` | An exact match was attempted on every required key, against every manifest file, and nothing matched | The list of sources searched and the keys attempted |
| `INCONCLUSIVE` | Something matched only weakly, or absence can't be established | A reason |
| `NOT CHECKABLE` | The paper did not publish enough to answer the question | The verbatim data-availability statement and a complete record of where we looked. **This is a finding, not an error** |

## What limits a verdict

- **A PMID or DOI identifies a paper, not a dataset.** One paper can publish several datasets (Zheng 2017 released a mouse-brain dataset and the PBMC data; Replogle 2022 released K562 and RPE1 screens). A match on the paper alone is capped at `INCONCLUSIVE` ("same publication"), and shows the accession the manifest lists so a person can compare. `PRESENT` needs a dataset-level match: the accession, or a verifier's recorded `identity_note` explaining why the paper-level match is the same dataset.
- **A title or keyword match is never enough.** It is capped at `INCONCLUSIVE`. Author names and titles collide. For example, "Zheng" matches a 2020 immune atlas as well as the Zheng68K benchmark.
- **A superset cannot prove presence.** When a paper names a versioned public corpus (e.g. a CELLxGENE census release) that the model trained on a *subset* of, absence from the corpus proves absence from the model's data, but presence proves nothing. The best such a match can reach is `INCONCLUSIVE`.
- **Absence needs every required key.** Some manifests leave identifiers blank; one had PMIDs missing for 161 of 522 studies. For those manifests, `NOT PRESENT` requires both the accession and the PMID to be searched.
- **Only confirmed identifiers count.** A dataset's accession must be confirmed against a primary source before a search on it counts. Searching the wrong accession is not a search.
- **Identity can be provisional.** Some datasets live outside GEO, and no primary source links their accession to the paper; the link rests on metadata such as a title and a submitter. Zheng68K's SRA experiment is an example. Such an identifier is searched, but any finding that relies on it carries the qualifier **provisional identity**, with the basis stated, e.g. `NOT PRESENT · provisional identity`. The verdict describes the search; the qualifier describes how sure we are which dataset was searched for.
- **A supplied file has to be confirmed.** A manifest file that was handed to us, rather than downloaded from the publisher, supports `PRESENT` or `NOT PRESENT` only after it is confirmed as the paper's file. Confirmation can come from matching the publisher's download, from its contents adding up to totals printed in the paper, or from the authors. Until then, the verdict is `INCONCLUSIVE`.
- **Every manifest file must be searched.** A `NOT PRESENT` from one of two files is not allowed.

## Guard codes

`corpus-checker validate` enforces these rules on every pull request. Each guard has a test that makes it fire.

| Code | Blocks a merge when |
|---|---|
| G00 | an entry doesn't match the schema (e.g. a superset corpus reporting `PRESENT`, or a verdict without its required evidence) |
| G01 | a title- or keyword-only match yields `PRESENT` or `NOT PRESENT` |
| G02 | a finding refers to a dataset that isn't in `datasets/` |
| G03 | a verdict, manifest or evaluation list changed but the verifier did not re-sign |
| G04 | a manifest file re-downloaded from its URL no longer matches its recorded hash *(weekly check; an unreachable URL only warns)* |
| G05 | *(warning)* a manifest was last checked more than 12 months ago |
| G06 | the regression test stops reproducing a known result (scFoundation / Norman 2019: 8 samples, 125,081 cells) |
| G07 | `sources_searched` names a file the manifest doesn't list |
| G08 | `NOT PRESENT` without searching every manifest file |
| G09 | `PRESENT` or `NOT PRESENT` rests on an unconfirmed, supplied file |
| G10 | `verified_by` is empty, or names someone not in `verifiers.yaml` |
| G11 | `NOT CHECKABLE` without every place a manifest could be published having been checked |
| G12 | an entry's id doesn't match its file name, or is duplicated |
| G13 | a finding uses a key the manifest doesn't carry |
| G14 | `NOT PRESENT` without attempting every required key |
| G15 | a finding claims an identifier search with no confirmed identifier behind it |
| G16 | the same dataset appears twice in one corpus's findings |
| G17 | *(warning)* a model's own evaluation dataset has no finding |
| G18 | *(warning)* `NOT PRESENT` while some discovery locations are unchecked |
| G19 | a manifest file's column roles name a column that doesn't exist |
| G20 | a committed corpus snapshot is missing or has changed |
| G21 | `PRESENT` rests on a paper-level match (PMID or DOI only) without a verifier's `identity_note` |
| G22 | a committed manifest extract is missing, or doesn't name the publisher file it was derived from |
| G23 | a finding's identity (provisional or not) disagrees with the catalog identifiers it searched |

Exit codes never encode verdicts: `0` means the check ran, `1` a tool error, `2` a rule violation and `3` bad usage.

## Looking up a dataset

`corpus-checker lookup` and the site's lookup page start from a dataset rather than a model. They answer two questions: *which models used this dataset in training?* and *is this dataset in model X?* They accept an accession, a PMID, a DOI, a URL containing one, or a catalog name, and they can take a list of datasets, such as a benchmark plan.

- The same rules apply as for a registry check. Each answer is **Yes / No / Can't tell / Unknown**, backed by one of the four verdicts, and says why.
- **Verification belongs to the model entries.** A lookup searches manifests a verifier has already recorded, and each answer names who recorded that entry and when. Draft entries are not searched on the site.
- **Every result lists which models were searched.** A model that isn't in the registry is not covered, so "No" never means "no model anywhere".
- Input is expanded through NCBI: a GEO series brings its PMID, DOI and SRA/BioProject IDs. A single sample (GSM) is never widened to its series. A PMID or DOI is answered at the paper level, and then separately for each dataset the paper links to.

> **PMIDs and DOIs identify papers, not datasets.** One paper can publish several datasets (Zheng 2017 released a mouse-brain dataset and the PBMC data; Replogle 2022 released K562 and RPE1 screens). A match on a PMID or DOI alone cannot tell which of them a model trained on. Search by accession (GSE…, E-MTAB…, a CELLxGENE ID) whenever you can.

To make an answer permanent, use the *Add a dataset* form. The dataset then gets its own page and appears in the models × datasets matrix.

## Who signs an entry

`verified_by` means **a person listed in [`verifiers.yaml`](../verifiers.yaml) read the evidence and agrees with the entry.** Code never fills it in. Drafts can be proposed but cannot be merged until a verifier signs. If anyone later changes a verdict, a manifest or a model's evaluation list, the entry is blocked until a verifier signs again (G03). A signature therefore always refers to the claim as it currently stands.

## Corrections

Every entry names its sources: the quoted availability statement, manifest URLs and hashes, the sources searched, the keys attempted and the matched sample IDs. A correction is therefore a change to a specific line.

- **Open an issue** using the *Correction* form. You don't need to write YAML; say which claim is wrong and link the evidence.
- **Or open a pull request** changing the entry directly. CI re-runs every guard, and a verifier re-signs before it merges.

Anyone can submit a correction, including a model's authors. We ask you to say how you're connected to the model, so readers have that context, but it doesn't limit who can submit.

## Disputes

Some objections aren't corrections. Someone may agree with every fact and still object to how an entry presents it. Use the *Dispute* form for these:

- Your statement is recorded **verbatim** in the entry's `disputed` block, with your name, the date, and any response.
- **The entry stays visible**, with the dispute attached. A contested entry is never deleted without notice.
- If the dispute leads to a factual change, that change goes through the correction process above.
