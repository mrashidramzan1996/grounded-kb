from kb.gitfiles import changed_files, default_branch, read_tree_files, repo_files
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
