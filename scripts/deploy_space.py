#!/usr/bin/env python3
"""Deploy the built static site to a Hugging Face Space.

Pushes the contents of a build folder (produced by `corpus-checker build-site`) to a
STATIC Hugging Face Space. GitHub remains the source of truth — this script only publishes
what the registry already contains; it never generates or judges content itself.

    python scripts/deploy_space.py --space-id user/corpus-checker --folder site/_build
    python scripts/deploy_space.py --space-id user/corpus-checker --folder site/_build --dry-run
    python scripts/deploy_space.py --space-id user/corpus-checker --folder site/_build --private

Auth: reads the token from the HF_TOKEN environment variable. Never pass it on the command
line and never echo it — see docs/DEPLOY.md. `--dry-run` needs neither a token nor network
access; it only reports what would be uploaded.

Exit codes: 0 deployed (or dry-run reported) · 1 a precondition failed (missing folder, missing
token) · 2 bad usage.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Protocol


class DeployError(RuntimeError):
    """A deploy precondition failed — e.g. no build folder, no HF_TOKEN outside --dry-run."""


class ApiLike(Protocol):
    """The slice of huggingface_hub.HfApi this script uses. A test fake only needs this much."""

    def create_repo(self, repo_id: str, **kwargs: Any) -> Any: ...
    def upload_folder(self, **kwargs: Any) -> Any: ...


# The front matter a STATIC Hugging Face Space needs to build at all. `short_description` is
# kept under the Hub's ~60-character display limit. `emoji` is a plain, neutral signifier —
# not a verdict on any model.
SPACE_README = """---
title: corpus-checker
emoji: \U0001f50e
colorFrom: blue
colorTo: gray
sdk: static
app_file: index.html
pinned: false
license: mit
short_description: Does eval data appear in a model's training corpus?
---

corpus-checker reports whether single-cell foundation models' evaluation datasets appear in
their published pretraining corpora, drawn from a curated, human-verified registry. It reports
exposure, not effect: overlap between a model's evaluation data and its training corpus does
not by itself mean a result is inflated.

**The GitHub repository is the source of truth.** This Space is a static build regenerated from
that repository on every merge and is never hand-edited. To report an error, correct an entry,
or suggest a model, open an issue or pull request on GitHub — not here.
"""

# Passed to HfApi.upload_folder to mirror-sync the Space: any remote file no longer produced by
# the build is removed. huggingface_hub guarantees `.gitattributes` is never deleted by
# delete_patterns even when it matches (confirmed via `help(HfApi.upload_folder)` against the
# installed 1.x package) — that guarantee, not an exclusion pattern here, is what protects it,
# since delete_patterns has no negation syntax to carve it out ourselves.
DELETE_PATTERNS = ["*"]


def write_space_readme(build_dir: Path) -> Path:
    """Write the Space's README.md (front matter + source-of-truth notice) into the build folder.

    Overwrites any README the site build itself produced — the Space front matter is not
    negotiable content, it is what makes the Space buildable at all.
    """
    build_dir.mkdir(parents=True, exist_ok=True)
    readme_path = build_dir / "README.md"
    readme_path.write_text(SPACE_README, encoding="utf-8")
    return readme_path


def iter_upload_files(build_dir: Path) -> list[Path]:
    """Every file in the build folder, as paths relative to it, sorted for stable output."""
    return sorted(p.relative_to(build_dir) for p in build_dir.rglob("*") if p.is_file())


def deploy(
    *,
    space_id: str,
    folder: Path | str,
    dry_run: bool = False,
    private: bool = False,
    token: str | None = None,
    sha: str | None = None,
    api: ApiLike | None = None,
) -> list[Path]:
    """Deploy `folder` to the Hugging Face Space `space_id`. Returns the files it uploaded (or,
    on --dry-run, the files it would have uploaded).

    `api` is injectable so tests can pass a fake HfApi-shaped object instead of the real
    huggingface_hub client — no network call and no import of huggingface_hub happens unless a
    real deploy is actually attempted.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise DeployError(f"build folder not found: {folder}")

    write_space_readme(folder)
    files = iter_upload_files(folder)

    if dry_run:
        print(f"[dry-run] would deploy {len(files)} file(s) to space '{space_id}':")
        for f in files:
            print(f"  {f}")
        print("[dry-run] no network calls made; HF_TOKEN not required.")
        return files

    if not token:
        raise DeployError(
            "HF_TOKEN is not set. Export it as an environment variable before running a real "
            "deploy (never pass it on the command line) — see docs/DEPLOY.md."
        )

    if api is None:
        from huggingface_hub import HfApi  # imported lazily: --dry-run needs no dependency import

        api = HfApi()

    api.create_repo(
        space_id,
        token=token,
        repo_type="space",
        exist_ok=True,
        space_sdk="static",
        private=private,
    )

    commit_sha = sha if sha is not None else os.environ.get("GITHUB_SHA", "local")
    api.upload_folder(
        repo_id=space_id,
        folder_path=str(folder),
        token=token,
        repo_type="space",
        commit_message=f"Deploy {commit_sha[:7]} from GitHub",
        delete_patterns=DELETE_PATTERNS,
    )
    return files


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deploy_space.py",
        description="Deploy a built static site to a Hugging Face Space.",
    )
    p.add_argument("--space-id", required=True, help="Space id, e.g. someuser/corpus-checker")
    p.add_argument("--folder", required=True, type=Path, help="Local build folder to upload, e.g. site/_build")
    p.add_argument("--dry-run", action="store_true", help="List what would be uploaded. No network call, no token needed.")
    p.add_argument("--private", action="store_true", help="Create the Space as private if it does not exist yet")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    token = os.environ.get("HF_TOKEN")
    try:
        deploy(
            space_id=args.space_id,
            folder=args.folder,
            dry_run=args.dry_run,
            private=args.private,
            token=token,
        )
    except DeployError as exc:
        print(f"deploy_space: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
