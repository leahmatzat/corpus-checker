"""Offline tests for scripts/deploy_space.py.

Everything here runs without network access and without the real huggingface_hub client ever
making a call — a FakeApi records what it was asked to do. `scripts/` is not part of the
installed `corpus_checker` package, so the module is loaded directly from its file path.
"""
from __future__ import annotations

import fnmatch
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("deploy_space", REPO / "scripts" / "deploy_space.py")
deploy_space = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(deploy_space)


class FakeApi:
    """Records every call. Also simulates the one documented huggingface_hub guarantee the
    script leans on — `.gitattributes` survives `delete_patterns` even when it matches — so the
    test for that behaves the same way the real 1.x client does (confirmed via
    `help(HfApi.upload_folder)` against the installed package, not assumed)."""

    def __init__(self, remote_files=None):
        self.remote_files = set(remote_files or set())
        self.create_repo_calls: list[tuple[str, dict]] = []
        self.upload_folder_calls: list[dict] = []

    def create_repo(self, repo_id, **kwargs):
        self.create_repo_calls.append((repo_id, kwargs))
        return object()

    def upload_folder(self, **kwargs):
        self.upload_folder_calls.append(kwargs)
        folder = Path(kwargs["folder_path"])
        uploaded = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}
        delete_patterns = kwargs.get("delete_patterns") or []
        for existing in list(self.remote_files):
            if existing == ".gitattributes" or existing in uploaded:
                continue
            if any(fnmatch.fnmatch(existing, pat) for pat in delete_patterns):
                self.remote_files.discard(existing)
        self.remote_files |= uploaded
        return object()


@pytest.fixture
def build_dir(tmp_path):
    d = tmp_path / "site" / "_build"
    d.mkdir(parents=True)
    (d / "index.html").write_text("<html></html>", encoding="utf-8")
    (d / "assets").mkdir()
    (d / "assets" / "style.css").write_text("body {}", encoding="utf-8")
    return d


# --------------------------------------------------------------------------- README front matter

def test_writes_readme_with_static_space_front_matter(build_dir):
    path = deploy_space.write_space_readme(build_dir)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "title: corpus-checker" in text
    assert "sdk: static" in text
    assert "app_file: index.html" in text
    assert "pinned: false" in text
    assert "license: mit" in text
    assert "short_description:" in text
    assert "emoji:" in text
    assert "colorFrom:" in text and "colorTo:" in text


def test_readme_paragraph_names_github_as_source_of_truth(build_dir):
    text = deploy_space.write_space_readme(build_dir).read_text(encoding="utf-8")
    assert "source of truth" in text.lower()
    assert "github" in text.lower()
    assert "correction" in text.lower() or "correct" in text.lower()


def test_short_description_is_short(build_dir):
    text = deploy_space.write_space_readme(build_dir).read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("short_description:"):
            assert len(line) <= 80  # generous; the Hub's real display limit is ~60
            break
    else:
        pytest.fail("short_description not found in README front matter")


def test_readme_overwrites_any_existing_file(build_dir):
    (build_dir / "README.md").write_text("whatever the site build produced", encoding="utf-8")
    deploy_space.write_space_readme(build_dir)
    assert "whatever the site build produced" not in (build_dir / "README.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- dry-run

def test_dry_run_makes_no_api_calls_and_needs_no_token(build_dir):
    fake = FakeApi()
    files = deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, dry_run=True, token=None, api=fake)
    assert fake.create_repo_calls == []
    assert fake.upload_folder_calls == []
    assert any(f.name == "index.html" for f in files)


def test_dry_run_still_writes_readme(build_dir):
    deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, dry_run=True, token=None, api=FakeApi())
    assert (build_dir / "README.md").exists()


def test_dry_run_lists_files_on_stdout(build_dir, capsys):
    deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, dry_run=True, token=None, api=FakeApi())
    out = capsys.readouterr().out
    assert "index.html" in out
    assert "dry-run" in out


def test_dry_run_needs_no_api_object_at_all(build_dir):
    # api=None and no HF_TOKEN import should ever be attempted for a dry run.
    files = deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, dry_run=True, token=None)
    assert any(f.name == "index.html" for f in files)


# --------------------------------------------------------------------------- missing token

def test_missing_token_raises_clear_error_outside_dry_run(build_dir):
    with pytest.raises(deploy_space.DeployError, match="HF_TOKEN"):
        deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, dry_run=False, token=None, api=FakeApi())


def test_missing_token_makes_no_api_calls(build_dir):
    fake = FakeApi()
    with pytest.raises(deploy_space.DeployError):
        deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, dry_run=False, token=None, api=fake)
    assert fake.create_repo_calls == []
    assert fake.upload_folder_calls == []


def test_cli_missing_token_exits_nonzero(build_dir, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    code = deploy_space.main(["--space-id", "user/corpus-checker", "--folder", str(build_dir)])
    assert code != 0


# ------------------------------------------------------------- create_repo / upload_folder args

def test_create_repo_arguments(build_dir):
    fake = FakeApi()
    deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, token="tok", api=fake, sha="abcdef1234")
    assert len(fake.create_repo_calls) == 1
    repo_id, kwargs = fake.create_repo_calls[0]
    assert repo_id == "user/corpus-checker"
    assert kwargs["repo_type"] == "space"
    assert kwargs["space_sdk"] == "static"
    assert kwargs["exist_ok"] is True
    assert kwargs["token"] == "tok"
    assert kwargs["private"] is False


def test_create_repo_private_flag(build_dir):
    fake = FakeApi()
    deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, token="tok", api=fake, private=True)
    _, kwargs = fake.create_repo_calls[0]
    assert kwargs["private"] is True


def test_upload_folder_arguments(build_dir):
    fake = FakeApi()
    deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, token="tok", api=fake, sha="abcdef1234")
    assert len(fake.upload_folder_calls) == 1
    kwargs = fake.upload_folder_calls[0]
    assert kwargs["repo_id"] == "user/corpus-checker"
    assert Path(kwargs["folder_path"]) == build_dir
    assert kwargs["repo_type"] == "space"
    assert kwargs["token"] == "tok"
    assert kwargs["commit_message"] == "Deploy abcdef1 from GitHub"


def test_upload_folder_commit_message_falls_back_to_local(build_dir, monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    fake = FakeApi()
    deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, token="tok", api=fake)
    assert fake.upload_folder_calls[0]["commit_message"] == "Deploy local from GitHub"


def test_upload_folder_commit_message_reads_github_sha_env(build_dir, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "0123456789abcdef")
    fake = FakeApi()
    deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, token="tok", api=fake)
    assert fake.upload_folder_calls[0]["commit_message"] == "Deploy 0123456 from GitHub"


# --------------------------------------------------------------------- .gitattributes never deleted

def test_gitattributes_never_deleted(build_dir):
    fake = FakeApi(remote_files={".gitattributes", "old_page.html"})
    deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, token="tok", api=fake)
    assert ".gitattributes" in fake.remote_files
    # and a file the build no longer produces IS gone, proving delete_patterns did something
    assert "old_page.html" not in fake.remote_files


def test_delete_patterns_passed_to_upload_folder(build_dir):
    fake = FakeApi()
    deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, token="tok", api=fake)
    assert fake.upload_folder_calls[0]["delete_patterns"] == deploy_space.DELETE_PATTERNS


# --------------------------------------------------------------------------- missing build folder

def test_missing_build_folder_raises(tmp_path):
    with pytest.raises(deploy_space.DeployError):
        deploy_space.deploy(space_id="user/corpus-checker", folder=tmp_path / "does-not-exist", dry_run=True, token=None)


# --------------------------------------------------------------------------- injectable api

def test_api_is_injectable_real_client_never_imported(build_dir):
    # If deploy() tried to import huggingface_hub here, this would still pass in an environment
    # that has it installed — the meaningful assertion is that our fake is what got called.
    fake = FakeApi()
    deploy_space.deploy(space_id="user/corpus-checker", folder=build_dir, token="tok", api=fake)
    assert len(fake.create_repo_calls) == 1
    assert len(fake.upload_folder_calls) == 1
