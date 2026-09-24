# Verifying an entry

How to review a registry entry and sign it off, and what to do about each problem the validator can report. For the rules themselves, see [RULES.md](RULES.md).

Run every command from the repository folder. `.venv/bin/corpus-checker` is the tool installed there (see the README's "Run it").

---

## 1. What signing means

`provenance.verified_by` says **a person listed in [`verifiers.yaml`](../verifiers.yaml) read the evidence and agrees with the entry as it stands now.** Code never fills it in.

Signing covers the whole entry: the paper, where we looked, the manifest and its files, the model's evaluation datasets, and every finding. If anyone later changes a verdict, a manifest or the evaluation list, the entry is blocked until a verifier signs again (guard G03). A signature therefore always refers to the current claim.

## 2. Find what needs you

```bash
.venv/bin/corpus-checker validate --base-ref main
```

This compares the branch with `main`. Each error names a guard code, a file and the exact field. The ones a verifier acts on:

| Code | What it means | What you do |
|---|---|---|
| **G03** | A signed entry changed since it was signed | Review the change (section 3) and re-sign (section 4) |
| **G10** | `verified_by` is empty: a draft | Review and sign, fix and then sign, or keep it out of this merge (section 5) |
| **G14** | `NOT PRESENT` without every key the manifest needs (usually a missing accession) | Find and confirm the dataset's accession in `datasets/`, or accept `INCONCLUSIVE` |
| **G09** | A verdict rests on a supplied file nobody has confirmed | Confirm the file (publisher download matches its hash, totals match the paper, or the authors confirm), or accept `INCONCLUSIVE` |
| **G21** | `PRESENT` on a paper-level match (PMID/DOI only) | Add an `identity_note` saying why it is the same dataset, or accept `INCONCLUSIVE` |
| **G23** | A finding's provisional identity disagrees with the catalog | Make `identity` / `identity_basis` match the dataset's identifiers |
| **G04** | A manifest re-downloaded from its URL no longer matches its hash | Download it, find out what changed, and re-check the entry |
| G05 *(warning)* | A manifest was last checked over a year ago | Re-check when convenient |
| G18 *(warning)* | `NOT PRESENT` while some places a manifest could live are unchecked | Check them, or accept the risk knowingly |

Any other code is a problem with how the entry is written; the message says which field to fix. The full list is in [RULES.md](RULES.md#guard-codes).

## 3. Review an entry

**See what changed** since the last signed version:

```bash
git diff main <branch> -- registry/<id>.yaml
```

For a pull request from someone else, run `git fetch origin` first and compare `origin/main` with `origin/<branch>`.

**Re-run the evidence.** This rebuilds every finding from the manifest and compares it with what the entry says:

```bash
.venv/bin/corpus-checker check <id> --rerun
```

If you have the publisher's original manifest files, point at them. That is the strongest check, because each file must match its recorded hash:

```bash
.venv/bin/corpus-checker check <id> --rerun --manifests ~/Downloads
```

No row should say **`[differs from registry]`**. Rows marked `[not in registry]` are catalog datasets the entry doesn't record yet. That's fine, or you can add them.

**Read the entry top to bottom.** Open the paper beside it.

- [ ] **Paper:** the published version (not the preprint, if both exist), with the right DOI and PMID.
- [ ] **Where we looked (`discovery`):** each location marked `checked: true` was actually searched for a training-data list. Open every supplementary file, not only the ones the availability statement mentions.
- [ ] **Manifest quote:** word for word the same as the paper's data-availability statement.
- [ ] **Manifest type:** the classification fits (a table of datasets, a versioned public corpus, named datasets in prose, or nothing).
- [ ] **Manifest files:** the URLs open, and the `sha256` values match your downloads (`shasum -a 256 <file>`).
- [ ] **`acquired` / `confirmation`:** true statements. "fetched" means downloaded from that URL. A supplied file's confirmation names real evidence.
- [ ] **`evaluated_on`:** the datasets the paper evaluates on, with the right tasks.
- [ ] **Each finding:** follows [RULES.md](RULES.md), and its reason reads correctly.
- [ ] **Provisional identity:** the stated basis is accurate.
- [ ] **Caveats:** accurate and neutral. The registry reports exposure, not effect.
- [ ] **Each dataset the entry refers to** (`datasets/<id>.yaml`): the identifiers are right, and each is marked confirmed or provisional honestly.

## 4. Sign

Set two lines in the entry's `provenance` block:

```yaml
provenance:
  verified_by: LM            # your initials, as listed in verifiers.yaml
  verified_date: 2026-09-24  # today
```

**From the terminal:**

```bash
.venv/bin/corpus-checker validate --base-ref main
```

```bash
git commit -am "Verify <model>: <what you checked>"
```

```bash
git push
```

**Or on GitHub, without a terminal:** open the pull request, go to **Files changed**, open the file's **⋯** menu and choose **Edit file**. Change the two lines, then **Commit changes** to the pull request's branch.

Either way, CI re-runs and that entry's errors clear.

## 5. When you're not ready to sign

Leave `verified_by` empty. A draft can't merge to `main`, and it is never deleted silently. Either:

- **fix it and sign**, or
- **keep it out of this merge.** A maintainer moves the entry to a follow-up branch, so everything else can merge. It comes back when it's ready.

## 6. Adding another verifier

Add them to [`verifiers.yaml`](../verifiers.yaml) and [`.github/CODEOWNERS`](../.github/CODEOWNERS) in one pull request, reviewed by an existing verifier. Nobody adds themselves.
