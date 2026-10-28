"""Exact-revision transfer without live network calls, Shared Skills Library v1."""
import importlib
from io import BytesIO
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / "library/skills/shared-skills-library/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def archive(files):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, content in files.items():
            member = zipfile.ZipInfo(name)
            # Python's Windows writer normalises backslashes for string names;
            # preserve exact wire names so malicious archive fixtures are real.
            member.filename = name
            member.orig_filename = name
            member.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(member, content)
    return output.getvalue()


class TransferTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("skill_library.transfer")
        except ModuleNotFoundError:
            self.fail("A validated transfer helper is needed before downloading an upstream update")

    def source(self, path="skills/useful"):
        return {"kind": "github", "repository": "owner/repo", "path": path}

    def test_complete_selected_package_with_root_licence_is_downloaded(self):
        data = archive({
            "repo-ref/skills/useful/SKILL.md": "---\nname: useful\ndescription: Useful\n---\n",
            "repo-ref/skills/useful/scripts/run.py": "print('example')\n",
            "repo-ref/skills/useful/references/guide.md": "Details\n",
            "repo-ref/LICENSE": "Publisher licence\n",
            "repo-ref/unrelated/large.md": "Do not extract\n",
        })
        urls = []
        def fetch(url):
            urls.append(url)
            return data
        with tempfile.TemporaryDirectory() as tmp:
            result = self.module().download_github_skill(self.source(), "a" * 40, Path(tmp), fetch)
            self.assertEqual(result.name, "useful")
            self.assertEqual((result / "scripts/run.py").read_text(), "print('example')\n")
            self.assertEqual((result / "LICENSE").read_text(), "Publisher licence\n")
            self.assertFalse((result / "unrelated").exists())
            self.assertTrue(urls[0].endswith("/zip/" + "a" * 40))

    def test_root_skill_and_directory_spaces_are_supported(self):
        for path, prefix in ((".", "repo-ref"), ("my skills/useful", "repo-ref/my skills/useful")):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                data = archive({prefix + "/SKILL.md": "---\nname: useful\n---\n", prefix + "/asset.txt": "asset"})
                result = self.module().download_github_skill(self.source(path), "b" * 40, Path(tmp), lambda url: data)
                self.assertEqual((result / "asset.txt").read_text(), "asset")

    def test_zip_executable_modes_survive_windows_extraction_outside_source(self):
        output = BytesIO()
        with zipfile.ZipFile(output, "w") as zipped:
            for path, content, mode in (
                ("repo-ref/skills/useful/SKILL.md", "---\nname: useful\n---\n", 0o644),
                ("repo-ref/skills/useful/bin/helper", "executable", 0o755),
                ("repo-ref/skills/useful/bin/readme", "not executable", 0o644),
                ("repo-ref/LICENSE", "licence", 0o755),
            ):
                member = zipfile.ZipInfo(path)
                member.create_system = 3
                member.external_attr = (stat.S_IFREG | mode) << 16
                zipped.writestr(member, content)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(Path, "chmod", return_value=None):
            result = self.module().download_github_skill(self.source(), "a" * 40, Path(tmp), lambda url: output.getvalue())
            metadata_path = result.parent / ".skill-library-transfer-modes.json"
            self.assertTrue(metadata_path.is_file(), "Windows must retain executable intent independently of filesystem chmod")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["executables"], ["bin/helper"])
            self.assertFalse((result / metadata_path.name).exists())
            self.assertEqual((result / "LICENSE").read_text(), "licence")

    def test_existing_transfer_metadata_is_not_overwritten(self):
        data = archive({"repo-ref/skills/useful/SKILL.md": "---\nname: useful\n---\n"})
        with tempfile.TemporaryDirectory() as tmp:
            metadata = Path(tmp) / ".skill-library-transfer-modes.json"
            metadata.write_text('{"executables":["preserve"]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                self.module().download_github_skill(self.source(), "a" * 40, Path(tmp), lambda url: data)
            self.assertEqual(json.loads(metadata.read_text())["executables"], ["preserve"])
            self.assertFalse((Path(tmp) / "useful").exists())

    def test_unsupported_sources_paths_and_mutable_refs_are_rejected_before_fetch(self):
        cases = [(self.source(), "main"), (self.source(), "abc1234"),
                 ({"kind": "unknown", "repository": "owner/repo"}, "a" * 40),
                 (self.source("../escape"), "a" * 40),
                 ({"kind": "github", "repository": "https://evil.invalid/repo"}, "a" * 40)]
        for source, revision in cases:
            with self.subTest(source=source, revision=revision), tempfile.TemporaryDirectory() as tmp:
                def fetch(url):
                    self.fail("Rejected source must not trigger a request")
                with self.assertRaises(ValueError):
                    self.module().download_github_skill(source, revision, Path(tmp), fetch)

    def test_every_archive_path_is_validated_even_outside_selected_subtree(self):
        bad_names = ["../escape.txt", "repo-ref/../escape.txt", "/absolute/file", "repo-ref/C:/file", "repo-ref/evil\\file"]
        for bad_name in bad_names:
            with self.subTest(path=bad_name), tempfile.TemporaryDirectory() as tmp:
                data = archive({"repo-ref/skills/useful/SKILL.md": "name: useful", bad_name: "unsafe"})
                with self.assertRaises(ValueError):
                    self.module().download_github_skill(self.source(), "a" * 40, Path(tmp), lambda url: data)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_missing_skill_mixed_roots_and_duplicate_paths_fail(self):
        cases = [archive({"repo-ref/README.md": "missing"}),
                 archive({"repo-ref/skills/useful/SKILL.md": "name: useful", "other-ref/file.txt": "mixed"})]
        duplicate = BytesIO()
        with zipfile.ZipFile(duplicate, "w") as zipped:
            zipped.writestr("repo-ref/skills/useful/SKILL.md", "name: useful")
            zipped.writestr("repo-ref/skills/useful/Case.txt", "one")
            zipped.writestr("repo-ref/skills/useful/case.txt", "two")
        cases.append(duplicate.getvalue())
        for data in cases:
            with self.subTest(size=len(data)), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    self.module().download_github_skill(self.source(), "a" * 40, Path(tmp), lambda url: data)

    def test_zip_symlinks_are_rejected(self):
        output = BytesIO()
        with zipfile.ZipFile(output, "w") as zipped:
            zipped.writestr("repo-ref/skills/useful/SKILL.md", "name: useful")
            link = zipfile.ZipInfo("repo-ref/elsewhere/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            zipped.writestr(link, "../../secret")
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            self.module().download_github_skill(self.source(), "a" * 40, Path(tmp), lambda url: output.getvalue())

    def test_expanded_archive_limit_and_existing_destination_are_preserved(self):
        module = self.module()
        # Test the same limit boundary cheaply by temporarily shrinking the configured limit.
        original = module.MAX_EXPANDED_BYTES
        module.MAX_EXPANDED_BYTES = 128
        try:
            data = archive({"repo-ref/skills/useful/SKILL.md": "x" * 129})
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
                module.download_github_skill(self.source(), "a" * 40, Path(tmp), lambda url: data)
        finally:
            module.MAX_EXPANDED_BYTES = original
        data = archive({"repo-ref/skills/useful/SKILL.md": "---\nname: useful\n---\n"})
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "useful"
            existing.mkdir()
            (existing / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                module.download_github_skill(self.source(), "a" * 40, Path(tmp), lambda url: data)
            self.assertEqual((existing / "keep.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
