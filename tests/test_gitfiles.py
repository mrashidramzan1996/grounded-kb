import pytest
from kb.gitfiles import GitError, changed_files, default_branch, read_tree_files, repo_files
from tests.helpers import git, make_git_repo


def test_repo_files_untracked_and_odd_names(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"docs/my spec ü.pdf": "x", ".gitignore": "ignored.txt\n"})
    (repo / "new.py").write_text("x")
    (repo / "ignored.txt").write_text("x")
    files = repo_files(repo)
    assert "docs/my spec ü.pdf" in files and "new.py" in files and "ignored.txt" not in files


def test_default_branch_and_changed_files(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"a.txt": "1", "docs/d.md": "d"})
    assert default_branch(repo) == "main"
    git(repo, "checkout", "-b", "feat")
    (repo / "b.txt").write_text("2")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "b")
    (repo / "a.txt").write_text("changed")
    (repo / "u.txt").write_text("u")
    assert changed_files(repo) == ["a.txt", "b.txt", "u.txt"]
    assert changed_files(repo, "HEAD") == ["a.txt", "u.txt"]


def test_read_tree_files(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"docs/d.md": "d", "other.md": "o"})
    assert read_tree_files(repo, "HEAD", "docs") == {"docs/d.md": b"d"}


def test_bad_base_raises(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"a.txt": "1"})
    with pytest.raises(GitError, match="nope"):
        changed_files(repo, "nope")


def test_merge_base_semantics(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"a.txt": "1"})
    git(repo, "checkout", "-b", "feat")
    (repo / "b.txt").write_text("b")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "b")
    git(repo, "checkout", "main")
    (repo / "m.txt").write_text("m")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "m")
    git(repo, "checkout", "feat")
    (repo / "u.txt").write_text("u")
    assert changed_files(repo, "main") == ["b.txt", "u.txt"]


def test_only_origin_main(tmp_path):
    (tmp_path / "s").mkdir()
    src = make_git_repo(None, tmp_path / "s", extra={"a.txt": "1"})
    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(src), str(clone))
    git(clone, "checkout", "-b", "feat")
    (clone / "b.txt").write_text("b")
    git(clone, "add", "-A")
    git(clone, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-m", "b")
    git(clone, "branch", "-D", "main")
    assert default_branch(clone) == "origin/main"
    assert changed_files(clone) == ["b.txt"]
    assert changed_files(clone, "main") == ["b.txt"]


def test_tag_and_commit_helpers(tmp_path):
    from kb.gitfiles import commit_time, create_tag, delete_tag, head_commit, is_dirty, tag_exists

    repo = make_git_repo(None, tmp_path, extra={"kb/a.txt": "1", "x.txt": "1"})
    assert head_commit(repo) == git(repo, "rev-parse", "HEAD").strip()
    assert commit_time(repo, "HEAD") == int(git(repo, "show", "-s", "--format=%ct", "HEAD"))
    assert not tag_exists(repo, "t1")
    create_tag(repo, "t1")
    assert tag_exists(repo, "t1")
    with pytest.raises(GitError):
        create_tag(repo, "t1")
    delete_tag(repo, "t1")
    assert not tag_exists(repo, "t1")
    with pytest.raises(GitError):
        delete_tag(repo, "t1")
    assert not is_dirty(repo, "kb/")
    (repo / "x.txt").write_text("2")
    assert not is_dirty(repo, "kb/")
    (repo / "kb/a.txt").write_text("2")
    assert is_dirty(repo, "kb/")
    with pytest.raises(GitError):
        head_commit(repo / "nowhere")
