"""Tests for dir_digest."""

import json
import os

import pytest

from dir_digest import IgnoreRules, _walk, digest_tree, file_digests


def build(root, files):
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return root


class TestDigestTree:
    def test_a_digest_is_a_hex_string(self, tmp_path):
        build(tmp_path, {"a.txt": "hello"})
        digest = digest_tree(tmp_path)
        assert len(digest) == 64
        assert all(character in "0123456789abcdef" for character in digest)

    def test_order_of_creation_does_not_matter(self, tmp_path):
        first = build(tmp_path / "one", {"a.txt": "x", "b/c.txt": "y"})
        second = build(tmp_path / "two", {"b/c.txt": "y", "a.txt": "x"})
        assert digest_tree(first) == digest_tree(second)

    def test_same_content_different_root_paths_match(self, tmp_path):
        first = build(tmp_path / "deep" / "one", {"a.txt": "same"})
        second = build(tmp_path / "two", {"a.txt": "same"})
        assert digest_tree(first) == digest_tree(second)

    def test_content_change_changes_the_digest(self, tmp_path):
        build(tmp_path, {"a.txt": "one"})
        before = digest_tree(tmp_path)
        (tmp_path / "a.txt").write_text("two", encoding="utf-8")
        assert digest_tree(tmp_path) != before

    def test_a_rename_changes_the_digest(self, tmp_path):
        build(tmp_path, {"a.txt": "same"})
        before = digest_tree(tmp_path)
        (tmp_path / "a.txt").rename(tmp_path / "b.txt")
        assert digest_tree(tmp_path) != before

    def test_an_empty_directory_has_a_digest(self, tmp_path):
        assert digest_tree(tmp_path) == digest_tree(tmp_path)
        assert digest_tree(tmp_path) != digest_tree(build(tmp_path / "other", {"a": "x"}))

    def test_ignored_files_do_not_count(self, tmp_path):
        build(tmp_path, {"a.txt": "x", "cache.pyc": "junk"})
        with_ignore = digest_tree(tmp_path, patterns=["*.pyc"], gitignore=False)
        build(tmp_path, {"cache.pyc": "different junk"})
        assert digest_tree(tmp_path, patterns=["*.pyc"], gitignore=False) == with_ignore

    def test_a_directory_pattern_ignores_the_whole_directory(self, tmp_path):
        build(tmp_path, {"a.txt": "x", "build/out.o": "junk", "build/deep/out.o": "junk"})
        assert digest_tree(tmp_path, patterns=["build/"], gitignore=False) == digest_tree(
            build(tmp_path / "clean", {"a.txt": "x"})
        )

    def test_negation_patterns_keep_a_file(self, tmp_path):
        build(tmp_path, {"a.log": "keep", "b.log": "drop"})
        digests = file_digests(tmp_path, patterns=["*.log", "!a.log"], gitignore=False)
        assert list(digests) == ["a.log"]

    def test_anchored_patterns_only_match_at_the_root(self, tmp_path):
        build(tmp_path, {"top.log": "x", "sub/top.log": "x"})
        assert list(file_digests(tmp_path, patterns=["/top.log"], gitignore=False)) == [
            "sub/top.log"
        ]

    def test_a_gitignore_file_is_read_by_default(self, tmp_path):
        build(tmp_path, {"a.txt": "x", "secret.key": "s", ".gitignore": "*.key\n"})
        digests = file_digests(tmp_path)
        assert "secret.key" not in digests
        assert ".gitignore" in digests

    def test_gitignore_can_be_ignored(self, tmp_path):
        build(tmp_path, {"a.txt": "x", "secret.key": "s", ".gitignore": "*.key\n"})
        assert "secret.key" in file_digests(tmp_path, gitignore=False)

    def test_explicit_patterns_add_to_the_gitignore_rules(self, tmp_path):
        build(tmp_path, {"a.txt": "x", "notes.md": "n", ".gitignore": "*.md\n"})
        digests = file_digests(tmp_path, patterns=["a.txt"])
        assert list(digests) == [".gitignore"]

    def test_another_algorithm(self, tmp_path):
        build(tmp_path, {"a.txt": "x"})
        assert len(digest_tree(tmp_path, algorithm="sha1")) == 40

    def test_a_file_where_a_directory_belongs_raises(self, tmp_path):
        path = tmp_path / "file.txt"
        path.write_text("x", encoding="utf-8")
        with pytest.raises(NotADirectoryError):
            digest_tree(path)

    def test_mtimes_are_irrelevant(self, tmp_path):
        build(tmp_path, {"a.txt": "x"})
        before = digest_tree(tmp_path)
        os.utime(tmp_path / "a.txt", (0, 0))
        assert digest_tree(tmp_path) == before

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_symlinks_are_skipped_by_default(self, tmp_path):
        build(tmp_path, {"a.txt": "x"})
        (tmp_path / "link.txt").symlink_to(tmp_path / "a.txt")
        digests = file_digests(tmp_path)
        assert list(digests) == ["a.txt"]

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_symlinks_can_be_followed(self, tmp_path):
        build(tmp_path, {"a.txt": "x"})
        (tmp_path / "link.txt").symlink_to(tmp_path / "a.txt")
        digests = file_digests(tmp_path, follow_symlinks=True)
        assert digests["link.txt"] == digests["a.txt"]

    def test_binary_content(self, tmp_path):
        build(tmp_path, {"blob.bin": b"\x00\xff\x10"})
        assert len(digest_tree(tmp_path)) == 64


class TestIgnoreRules:
    def test_an_empty_rule_set_ignores_nothing(self):
        rules = IgnoreRules()
        assert len(rules) == 0
        assert rules.match("anything.txt") is False

    def test_comments_and_blanks_are_skipped(self):
        assert len(IgnoreRules(["# note", "", "   "])) == 0

    def test_a_simple_glob_matches_any_depth(self):
        rules = IgnoreRules(["*.pyc"])
        assert rules.match("a.pyc")
        assert rules.match("deep/down/a.pyc")
        assert not rules.match("a.py")

    def test_a_pattern_with_a_slash_is_a_path(self):
        rules = IgnoreRules(["docs/*.md"])
        assert rules.match("docs/a.md")
        assert not rules.match("other/docs/a.md")

    def test_double_star_matches_any_depth(self):
        rules = IgnoreRules(["docs/**/a.md"])
        assert rules.match("docs/a.md")
        assert rules.match("docs/x/y/a.md")

    def test_a_directory_only_rule_does_not_match_a_file(self):
        rules = IgnoreRules(["build/"])
        assert rules.match("build", is_dir=True)
        assert not rules.match("build", is_dir=False)

    def test_last_matching_rule_wins(self):
        rules = IgnoreRules(["*.log", "!keep.log"])
        assert rules.match("a.log") is True
        assert rules.match("keep.log") is False

    def test_a_later_ignore_beats_an_earlier_negation(self):
        rules = IgnoreRules(["!keep.log", "*.log"])
        assert rules.match("keep.log") is True

    def test_question_mark_matches_one_character(self):
        rules = IgnoreRules(["a?.txt"])
        assert rules.match("ab.txt")
        assert not rules.match("abc.txt")

    def test_a_character_class(self):
        rules = IgnoreRules(["file[0-9].txt"])
        assert rules.match("file3.txt")
        assert not rules.match("filex.txt")

    def test_an_unclosed_bracket_is_literal(self):
        rules = IgnoreRules(["a[.txt"])
        assert rules.match("a[.txt")

    def test_from_a_gitignore_file(self, tmp_path):
        ignore = tmp_path / ".gitignore"
        ignore.write_text("*.pyc\n!keep.pyc\n", encoding="utf-8")
        rules = IgnoreRules.from_gitignore(tmp_path)
        assert rules.match("a.pyc")
        assert not rules.match("keep.pyc")

    def test_from_a_missing_gitignore_file(self, tmp_path):
        assert len(IgnoreRules.from_gitignore(tmp_path)) == 0

    def test_from_an_explicit_path(self, tmp_path):
        ignore = tmp_path / "custom.ignore"
        ignore.write_text("*.tmp\n", encoding="utf-8")
        assert IgnoreRules.from_gitignore(ignore).match("a.tmp")


class TestCommandLine:
    def run(self, argv):
        from dir_digest.__main__ import main

        return main(argv)

    def test_prints_a_digest(self, tmp_path, capsys):
        build(tmp_path, {"a.txt": "x"})
        assert self.run([str(tmp_path)]) == 0
        assert len(capsys.readouterr().out.strip()) == 64

    def test_files_mode(self, tmp_path, capsys):
        build(tmp_path, {"a.txt": "x", "b/c.txt": "y"})
        assert self.run([str(tmp_path), "--files"]) == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 2
        assert lines[0].endswith("a.txt")

    def test_json_mode(self, tmp_path, capsys):
        build(tmp_path, {"a.txt": "x"})
        assert self.run([str(tmp_path), "--json"]) == 0
        assert list(json.loads(capsys.readouterr().out)) == ["a.txt"]

    def test_ignore_flag(self, tmp_path, capsys):
        build(tmp_path, {"a.txt": "x", "b.pyc": "y"})
        assert self.run([str(tmp_path), "--files", "--ignore", "*.pyc"]) == 0
        assert "b.pyc" not in capsys.readouterr().out

    def test_no_gitignore_flag(self, tmp_path, capsys):
        build(tmp_path, {"a.key": "x", ".gitignore": "*.key\n"})
        assert self.run([str(tmp_path), "--files", "--no-gitignore"]) == 0
        assert "a.key" in capsys.readouterr().out

    def test_a_missing_directory_exits_two(self, tmp_path, capsys):
        assert self.run([str(tmp_path / "nope")]) == 2
        assert "not a directory" in capsys.readouterr().err

    def test_an_unknown_algorithm_exits_two(self, tmp_path, capsys):
        assert self.run([str(tmp_path), "--algorithm", "nope"]) == 2
        assert "unsupported algorithm" in capsys.readouterr().err

    def test_the_default_directory_is_the_current_one(self, tmp_path, capsys, monkeypatch):
        build(tmp_path, {"a.txt": "x"})
        monkeypatch.chdir(tmp_path)
        assert self.run([]) == 0
        assert len(capsys.readouterr().out.strip()) == 64


class TestRigour:
    """Patterns and paths that the first pass did not exercise."""

    def test_a_double_star_inside_a_pattern(self):
        from dir_digest import _translate

        assert _translate("a**b") == "a.*b"
        assert _translate("**/x") == "(?:.*/)?x"
        assert _translate("x/**") == "x/.*"

    def test_a_negated_character_class(self):
        from dir_digest import _translate

        assert _translate("[!ab].txt") == r"[^ab]\.txt"

    def test_an_unclosed_character_class(self):
        from dir_digest import _translate

        assert _translate("a[b") == r"a\[b"

    def test_patterns_that_reduce_to_nothing(self):
        assert IgnoreRules(["!", "/", "   ", "# comment"]).rules == []

    def test_a_negation_keeps_a_file(self, tmp_path):
        build(tmp_path, {"keep.txt": "x", "drop.txt": "y"})
        rules = IgnoreRules(["*.txt", "!keep.txt"])
        assert [name for name, _ in _walked(tmp_path, rules)] == ["keep.txt"]

    def test_a_directory_only_pattern(self, tmp_path):
        build(tmp_path, {"build/a.txt": "x", "keep/b.txt": "y", "build.txt": "z"})
        rules = IgnoreRules(["build/"])
        names = [name for name, _ in _walked(tmp_path, rules)]
        assert names == ["build.txt", "keep/b.txt"]

    def test_an_anchored_pattern(self, tmp_path):
        build(tmp_path, {"a.log": "x", "sub/a.log": "y"})
        rules = IgnoreRules(["/a.log"])
        assert [name for name, _ in _walked(tmp_path, rules)] == ["sub/a.log"]

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need a privilege on Windows")
    def test_a_symlinked_file_is_not_followed(self, tmp_path):
        build(tmp_path, {"real.txt": "x"})
        (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
        rules = IgnoreRules([])
        assert [name for name, _ in _walked(tmp_path, rules)] == ["real.txt"]


def _walked(root, rules):
    from dir_digest import _walk

    return _walk(root, rules, False)


class TestRigour2:
    """Symlink handling and the error path of the CLI."""

    def test_a_symlink_is_skipped_but_followed_on_request(self, tmp_path, monkeypatch):
        from pathlib import Path

        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        rules = IgnoreRules()
        monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "a.txt", raising=True)
        assert _walk(tmp_path, rules, False) == []
        assert [name for name, _ in _walk(tmp_path, rules, True)] == ["a.txt"]

    def test_a_root_that_is_not_a_directory_exits_two(self, tmp_path, capsys):
        from dir_digest.__main__ import main

        path = tmp_path / "file.txt"
        path.write_text("x", encoding="utf-8")
        assert main([str(path)]) == 2
        assert "not a directory" in capsys.readouterr().err

    def test_an_oserror_while_hashing_exits_two(self, tmp_path, monkeypatch, capsys):
        from dir_digest import __main__ as cli

        def refuse(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(cli, "file_digests", refuse)
        assert cli.main([str(tmp_path), "--files"]) == 2
        assert "denied" in capsys.readouterr().err
