"""`corpus-checker review <model>` — a person records a review of one registry entry.

A review means a named person looked at what the tool can't check (the five REVIEW_CHECKS) and
found nothing wrong. The mechanical checks — manifest hashes, re-running every finding, the
rules — are the tool's job and run on every change, so this command shows their result instead
of asking the reviewer to redo them.

It only runs in a person's own terminal: with no TTY on stdin (CI, scripts, an assistant's
shell) it refuses, so code can never review an entry. It edits only the entry's `review:` block,
as text, so comments and formatting elsewhere in the file are untouched.
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .check import SourceError, check_corpus, compare, locate_committed
from .ledger import build_ledger, judgment_calls
from .registry import REVIEW_CHECKS, load_catalog, load_reviewers, load_yaml, review_of
from .validate import ERROR, Options, validate

NOT_A_TERMINAL = ("corpus-checker review records a person's review, so it only runs in your own terminal — "
                  "never from a script, CI or an assistant. Open a terminal and run it there.")

# Guards a review is expected to settle, or that are only warnings about scope.
_SETTLED_BY_REVIEW = {"G03", "G10", "G25"}


def review_block(by: str, date: str, checked: list[str], note_lines: list[str]) -> list[str]:
    return ["review:", f"  by: {by}", f"  date: {date}", f"  checked: [{', '.join(checked)}]", *note_lines]


def rewrite_review(text: str, by: str, date: str, checked: list[str]) -> str:
    """Replace only the top-level `review:` block; keep any `note:` it already has."""
    lines = text.split("\n")
    try:
        start = lines.index("review:")
    except ValueError:
        raise ValueError("the entry has no top-level `review:` block (is it schema v3?)") from None
    end = start + 1
    while end < len(lines) and lines[end].startswith("  "):
        end += 1
    old = lines[start + 1:end]
    note_at = next((i for i, ln in enumerate(old) if ln.startswith("  note:")), None)
    note_lines = old[note_at:] if note_at is not None else []
    return "\n".join(lines[:start] + review_block(by, date, checked, note_lines) + lines[end:])


def _rerun_summary(root: Path, entry: dict) -> list[str]:
    catalog = load_catalog(root)
    out = []
    for corpus in entry["corpora"]:
        sources = corpus["manifest"].get("sources") or []
        if corpus["manifest"]["type"] in ("C-vague", "D") or not sources:
            out.append(f"{corpus['stage']}: no manifest to re-run.")
            continue
        if not all(s.get("snapshot") or s.get("extract") for s in sources):
            out.append(f"{corpus['stage']}: manifest not stored in the repo; re-run it with "
                       f"`corpus-checker check {entry['id']} --rerun --manifests DIR`.")
            continue
        try:
            run = check_corpus(entry, corpus, catalog, locate_committed(root))
        except SourceError as exc:
            out.append(f"{corpus['stage']}: could not re-run ({exc}).")
            continue
        recorded = {f["dataset"] for f in corpus["result"]["findings"]}
        diffs = compare(corpus, run)
        missing = sorted(r.dataset for r in run.results if r.dataset not in recorded)
        line = f"{corpus['stage']}: {len(recorded)} recorded finding(s) re-run from the committed manifest; "
        line += "all match." if not diffs else f"{len(diffs)} difference(s): " + "; ".join(
            f"{d.dataset} {d.field} {d.registry!r} → {d.engine!r}" for d in diffs)
        if missing:
            line += f" Catalog datasets not recorded: {', '.join(missing)}."
        out.append(line)
    return out


def _changed_files(root: Path, entry_rel: str, base_ref: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), "diff", "--stat", base_ref, "--", entry_rel, "datasets/"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return [f"(could not compare with {base_ref})"]
    return out.splitlines() or [f"nothing changed since {base_ref}"]


def _yes(answer: str) -> bool:
    return answer.strip().lower() in ("y", "yes")


def run_review(root: Path, model_id: str, base_ref: str = "main", *,
               ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
               is_terminal: Callable[[], bool] = lambda: sys.stdin.isatty(),
               today: dt.date | None = None) -> int:
    """Walk a reviewer through one entry and record what they reviewed. Returns an exit code."""
    if not is_terminal():
        say(NOT_A_TERMINAL)
        return 3
    path = root / "registry" / f"{model_id}.yaml"
    if not path.is_file():
        say(f"No entry registry/{model_id}.yaml.")
        return 3
    entry = load_yaml(path)
    rel = f"registry/{model_id}.yaml"

    try:
        issues = validate(root, opts=Options(base_ref=base_ref))
    except RuntimeError:   # no such ref (or no git): check without the comparison
        issues = validate(root, opts=Options())
    blocking = [i for i in issues if i.path == rel and i.severity == ERROR and i.code not in _SETTLED_BY_REVIEW]
    if blocking:
        say(f"{rel} has problems a review can't settle. Fix these first:")
        for i in blocking:
            say(f"  {i.code} {i.where}: {i.message}")
        return 2

    by, date, checked = review_of(entry)
    say(f"Reviewing {entry['model']} ({rel}).")
    last = by or "none"
    if date:
        last += f", {date}"
    if checked:
        last += " · covered: " + ", ".join(checked)
    say(f"Last review: {last}.\n")
    say(f"What changed since {base_ref}:")
    for line in _changed_files(root, rel, base_ref):
        say(f"  {line}")
    say("\nWhat the tool checked (you don't need to redo this):")
    for line in _rerun_summary(root, entry):
        say(f"  {line}")
    calls = judgment_calls(build_ledger(root), model_id)
    say("\nAnswer y or n for each check you looked at yourself. Leave out anything you didn't; "
        "it will show as not reviewed.\n")

    ticked: list[str] = []
    for key, text in REVIEW_CHECKS.items():
        if key == "judgment_calls":
            say("The judgment calls in this entry:" if calls else "This entry has no judgment calls listed.")
            for c in calls:
                say(f"  - {c}")
        if _yes(ask(f"{text} [y/n] ")):
            ticked.append(key)

    reviewers = load_reviewers(root)
    initials = ask("\nYour initials, as listed in reviewers.yaml: ").strip()
    if initials not in reviewers:
        say(f"{initials!r} isn't in reviewers.yaml. Nothing was written.")
        return 3
    if not ticked:
        say("You didn't tick any check, so there's no review to record. Nothing was written.")
        return 0
    when = (today or dt.date.today()).isoformat()
    say(f"\nRecord: reviewed by {initials} on {when}; covered: {', '.join(ticked)}.")
    if not _yes(ask("Write this to the entry? [y/n] ")):
        say("Nothing was written.")
        return 0

    path.write_text(rewrite_review(path.read_text(encoding="utf-8"), initials, when, ticked), encoding="utf-8")
    say(f"\nWritten to {rel}. Check the change, then commit it:")
    say(f"  git -C {root} --no-pager diff -- {rel}")
    say(f"  git -C {root} commit -m \"Review {entry['model']}: {', '.join(ticked)}\" -- {rel}")
    return 0
