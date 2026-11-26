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


def test_branch_tip_looks_only_among_branches(tmp_path):
    from kb.gitfiles import branch_tip

    repo = make_git_repo(None, tmp_path, extra={"a.txt": "1"})
    main = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-ref", "refs/remotes/origin/release", main)
    assert branch_tip(repo, "main") == ("main", main)
    assert branch_tip(repo, "release") == ("origin/release", main)  # a CI checkout: origin's branch
    assert branch_tip(repo, "origin/release") == ("origin/release", main)
    assert branch_tip(repo, "refs/remotes/origin/release") == ("refs/remotes/origin/release", main)
    assert branch_tip(repo, "nope") is None and branch_tip(repo, "-x") is None
    assert branch_tip(repo, "HEAD") is None and branch_tip(repo, main) is None  # not branches
    git(repo, "tag", "v1")
    with pytest.raises(GitError, match="a tag named 'v1'"):  # a tag is not a branch, nor a guess
        branch_tip(repo, "v1")
    git(repo, "tag", "refs/heads/feature")  # no local feature: git rev-parse refs/heads/feature would find the tag
    with pytest.raises(GitError, match="a tag named 'refs/heads/feature'"):
        branch_tip(repo, "refs/heads/feature")
    git(repo, "branch", "feature")
    git(repo, "update-ref", "refs/remotes/origin/feature", main)
    with pytest.raises(GitError, match="a tag named 'refs/heads/feature'"):
        branch_tip(repo, "feature")
    git(repo, "tag", "-d", "refs/heads/feature")
    assert branch_tip(repo, "feature") == ("feature", main)  # local and origin's agree
    (repo / "b.txt").write_text("2")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "b")
    git(repo, "branch", "-f", "feature", "HEAD")
    with pytest.raises(GitError, match="ambiguous: refs/heads/feature and refs/remotes/origin/feature"):
        branch_tip(repo, "feature")
    assert branch_tip(repo, "refs/heads/feature")[0] == "refs/heads/feature"
    git(repo, "tag", "origin/release")
    with pytest.raises(GitError, match="a tag named 'origin/release' shadows the branch"):
        branch_tip(repo, "release")  # found as origin/release, which the tag shadows
    assert branch_tip(repo, "refs/remotes/origin/release") == ("refs/remotes/origin/release", main)


def _show_each(root, ref, prefix):
    """The 0.4.0 implementation: one `git show` per file."""
    from kb.gitfiles import _git, _split
    out = _git(root, "ls-tree", "-r", "-z", "--name-only", ref, "--", prefix).stdout
    return {p: _git(root, "show", f"{ref}:{p}").stdout for p in _split(out)}


def test_batch_read_matches_show_for_odd_paths(tmp_path):
    import os
    repo = make_git_repo(None, tmp_path, extra={"docs/plain.md": "p", "docs/with space.md": "s", "docs/üñí.md": "u",
                                                "docs/-dash.md": "d", "docs/nl\nname.md": "n", "docs/empty.md": "",
                                                "other/o.md": "o"})
    (repo / "docs" / "blob.bin").write_bytes(bytes(range(256)) + b"\n\n")
    os.symlink("plain.md", repo / "docs" / "link")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "odd")
    for prefix in ("docs", ".", "docs/-dash.md", "nothing"):
        got = read_tree_files(repo, "HEAD", prefix)
        assert got == _show_each(repo, "HEAD", prefix)
    got = read_tree_files(repo, "HEAD", "docs")
    assert got["docs/link"] == b"plain.md" and got["docs/nl\nname.md"] == b"n" and got["docs/empty.md"] == b""
    assert read_tree_files(repo, "HEAD", ".").keys() >= {"other/o.md", "docs/blob.bin"}


def test_read_tree_files_spawns_two_processes(tmp_path, monkeypatch):
    import subprocess
    repo = make_git_repo(None, tmp_path, extra={f"docs/f{i}.md": str(i) for i in range(20)})
    calls = []
    real = subprocess.run

    def counting(*a, **k):
        calls.append(a[0])
        return real(*a, **k)

    monkeypatch.setattr(subprocess, "run", counting)
    assert len(read_tree_files(repo, "HEAD", "docs")) == 20
    assert len(calls) == 2, calls
    assert calls[1][-2:] == ["cat-file", "--batch"]


def test_read_tree_files_bad_ref_raises(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"a.md": "a"})
    with pytest.raises(GitError):
        read_tree_files(repo, "nope", ".")


def test_lists_skip_gitlinks(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"docs/a.md": "a"})
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{git(repo, 'rev-parse', 'HEAD').strip()},docs/sub")
    git(repo, "commit", "-m", "gitlink")
    assert read_tree_files(repo, "HEAD", "docs") == {"docs/a.md": b"a"}


def test_blob_with_a_header_like_line_round_trips(tmp_path):
    body = "x\n" + "0" * 40 + " blob 5\nabcde\n" + "1" * 40 + " missing\n"
    repo = make_git_repo(None, tmp_path, extra={"docs/a.md": body, "docs/b.md": "b"})
    assert read_tree_files(repo, "HEAD", "docs") == {"docs/a.md": body.encode(), "docs/b.md": b"b"}


def test_file_at_is_none_for_a_gitlink_but_policy_file_at_raises(tmp_path):
    from kb.gitfiles import file_at, policy_file_at
    repo = make_git_repo(None, tmp_path, extra={"docs/a.md": "a"})
    head = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},docs/sub")
    git(repo, "commit", "-m", "gitlink")
    assert file_at(repo, "HEAD", "docs/sub") is None and file_at(repo, "HEAD", "docs") is None
    for path in ("docs/sub", "docs"):
        with pytest.raises(GitError, match="not a file"):
            policy_file_at(repo, "HEAD", path)
    assert policy_file_at(repo, "HEAD", "docs/a.md") == b"a" and policy_file_at(repo, "HEAD", "none") is None
