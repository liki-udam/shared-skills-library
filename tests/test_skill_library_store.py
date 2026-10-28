"""Behavioural package-store tests for Shared Skills Library v1.0.0."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "library/skills/shared-skills-library/scripts"
sys.path.insert(0, str(SCRIPTS))
from skill_library.store import Library, LibraryError, entry_id, fingerprint, read_skill


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "Drive" / "Shared Skills"
        self.lib = Library(self.root)
        self.source = self.base / "source"
        self.source.mkdir()
        self.write("SKILL.md", "---\nname: sample-skill\ndescription: A sample workflow\nversion: 1.0.0\n---\n\nUse references and scripts.\n")
        self.write("scripts/helper.py", "print('hello')\n")
        self.write("references/guide.md", "Supporting instructions.\n")
        self.write("LICENSE", "Example licence\n")
        self.meta = {
            "source": {"kind": "github", "repository": "owner/repo", "path": "skills/sample-skill", "revision": "abc123"},
            "requirements": ["Python 3.9+"], "accounts_costs": "No account required",
        }

    def write(self, relative, content):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def record_path(self, record):
        return self.root / "data/skill-library/entries" / (record["id"] + ".json")

    def manifest_with_added_path(self, entry, path):
        changed = deepcopy(entry)
        changed["package"]["files"].append({"path": path, "sha256": "a" * 64})
        # Supply a self-consistent checksum so these tests isolate portable path
        # validation, rather than passing merely because a checksum is stale.
        manifest_contents = [{"path": item["path"], "sha256": item["sha256"]} for item in changed["package"]["files"]]
        serialised = json.dumps(manifest_contents, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        changed["package"]["hash"] = hashlib.sha256(serialised.encode("utf-8")).hexdigest()
        return changed

    def test_publish_and_install_copies_the_complete_package_and_origin(self):
        self.write(".env.example", "EXAMPLE=replace-me\n")
        self.write(".venv/ignored.txt", "local environment")
        self.write("node_modules/ignored.txt", "local dependency")
        entry = self.lib.publish(self.source, self.meta)
        self.assertEqual(entry["name"], "sample-skill")
        self.assertEqual(entry["source"]["repository"], "owner/repo")
        result = self.lib.install(entry["id"], self.base / "device" / "skills")
        installed = Path(result["path"])
        self.assertEqual((installed / "scripts/helper.py").read_bytes(), (self.source / "scripts/helper.py").read_bytes())
        self.assertTrue((installed / "references/guide.md").is_file())
        self.assertTrue((installed / "LICENSE").is_file())
        self.assertTrue((installed / ".env.example").is_file())
        self.assertFalse((installed / ".venv").exists())
        self.assertFalse((installed / "node_modules").exists())
        self.assertEqual(result["hash"], fingerprint(installed))
        self.assertEqual(self.lib.verify(entry["id"])["status"], "verified")

    def test_corrupt_or_incomplete_drive_copy_never_replaces_a_local_skill(self):
        entry = self.lib.publish(self.source, self.meta)
        skills = self.base / "device" / "skills"
        target = skills / "sample-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("previous local skill", encoding="utf-8")
        package = self.root / entry["package"]["path"]
        (package / "scripts/helper.py").write_text("tampered", encoding="utf-8")
        with self.assertRaises(LibraryError):
            self.lib.install(entry["id"], skills, replace=True)
        self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "previous local skill")

    def test_unlisted_package_files_are_rejected(self):
        entry = self.lib.publish(self.source, self.meta)
        (self.root / entry["package"]["path"] / "unlisted.txt").write_text("extra", encoding="utf-8")
        with self.assertRaises(LibraryError):
            self.lib.install(entry["id"], self.base / "skills")

    def test_modified_install_requires_explicit_replacement_and_preserves_backup(self):
        entry = self.lib.publish(self.source, self.meta)
        skills = self.base / "device" / "skills"
        initial = self.lib.install(entry["id"], skills)
        modified = Path(initial["path"]) / "references/guide.md"
        modified.write_text("my local change", encoding="utf-8")
        with self.assertRaises(LibraryError):
            self.lib.install(entry["id"], skills)
        result = self.lib.install(entry["id"], skills, replace=True)
        backup = Path(result["backup_path"])
        self.assertEqual((backup / "references/guide.md").read_text(encoding="utf-8"), "my local change")
        self.assertNotEqual(backup.parent, skills)
        self.assertEqual(fingerprint(Path(result["path"])), entry["package"]["hash"])

    def test_new_revision_is_not_silently_promoted(self):
        old = self.lib.publish(self.source, self.meta)
        self.write("references/guide.md", "a changed version")
        with self.assertRaises(LibraryError):
            self.lib.publish(self.source, self.meta)
        self.assertEqual(self.lib.get(old["id"])["package"]["hash"], old["package"]["hash"])
        newer = self.lib.publish(self.source, self.meta, promote=True)
        self.assertNotEqual(newer["package"]["hash"], old["package"]["hash"])
        self.assertTrue((self.root / old["package"]["path"]).is_dir())
        self.assertEqual(len(newer["versions"]), 2)

    def test_identical_publication_is_idempotent_even_when_the_clock_changes(self):
        with patch("skill_library.store._now", return_value="2026-10-27T01:00:00+00:00"):
            first = self.lib.publish(self.source, self.meta)
        with patch("skill_library.store._now", return_value="2026-10-28T01:00:00+00:00"):
            repeated = self.lib.publish(self.source, self.meta)
        self.assertEqual(repeated, first)
        self.assertEqual(len(repeated["versions"]), 1)

    def test_explicit_transfer_executables_survive_windows_without_changing_content_hash(self):
        launch = self.write("tools/launch", "#!/bin/sh\nprintf hello\\n\n")
        launch.chmod(0o644)
        original_hash = fingerprint(self.source)
        metadata = deepcopy(self.meta)
        metadata["portable_executables"] = ["tools/launch"]
        entry = self.lib.publish(self.source, metadata)
        launch_record = next(item for item in entry["package"]["files"] if item["path"] == "tools/launch")
        self.assertIs(launch_record["executable"], True)
        self.assertEqual(entry["package"]["hash"], original_hash)
        self.assertEqual(self.lib.verify(entry["id"])["status"], "verified")

    def test_invalid_or_absent_transfer_executable_paths_fail_before_package_writes(self):
        for invalid in (["../outside"], ["/absolute"], ["scripts/missing"],
                        ["references"], ["scripts\\helper.py"], "scripts/helper.py", [None]):
            with self.subTest(paths=invalid):
                metadata = deepcopy(self.meta)
                metadata["portable_executables"] = invalid
                with self.assertRaises(LibraryError):
                    self.lib.publish(self.source, metadata)
                self.assertFalse((self.root / "library/packages").exists())
                self.assertEqual(self.lib.entries(), [])

    def test_catalogue_reads_validate_lexically_without_resolving_every_package_path(self):
        entry = self.lib.publish(self.source, self.meta)
        with patch("pathlib.Path.resolve", side_effect=AssertionError("catalogue reads should not resolve package paths")):
            listed = self.lib.entries()
        self.assertEqual(listed, [entry])

    def test_failed_final_move_restores_the_previous_installation(self):
        entry = self.lib.publish(self.source, self.meta)
        skills = self.base / "device" / "skills"
        target = skills / "sample-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("original local installation", encoding="utf-8")
        from skill_library import store
        real_replace = store.os.replace

        def failing_replace(source, destination):
            if ".shared-skills-library-staging" in str(source):
                raise OSError("simulated final move failure")
            return real_replace(source, destination)

        with patch("skill_library.store.os.replace", side_effect=failing_replace):
            with self.assertRaises(OSError):
                self.lib.install(entry["id"], skills, replace=True)
        self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "original local installation")

    def test_same_name_different_origins_is_ambiguous(self):
        first = self.lib.publish(self.source, self.meta)
        second_meta = deepcopy(self.meta)
        second_meta["source"]["repository"] = "another/publisher"
        second = self.lib.publish(self.source, second_meta)
        self.assertNotEqual(first["id"], second["id"])
        with self.assertRaises(LibraryError):
            self.lib.get("sample-skill")
        self.assertEqual(self.lib.get(second["id"])["id"], second["id"])

    def test_stale_record_update_does_not_overwrite_newer_information(self):
        first = self.lib.publish(self.source, self.meta)
        current = deepcopy(first)
        current["description"] = "newer information"
        self.lib.save_entry(current, expected=first)
        stale = deepcopy(first)
        stale["description"] = "stale writer"
        with self.assertRaises(LibraryError):
            self.lib.save_entry(stale, expected=first)
        self.assertEqual(self.lib.get(first["id"])["description"], "newer information")

    def test_drive_conflicted_record_is_reported_instead_of_selected(self):
        first = self.lib.publish(self.source, self.meta)
        conflict = self.record_path(first).with_name(first["id"] + " (conflicted copy).json")
        conflict.write_text(json.dumps(first), encoding="utf-8")
        with self.assertRaises(LibraryError):
            self.lib.entries()

    def test_plugin_child_selection_routes_to_entire_parent(self):
        plugin = {"id": "design-tools@publisher", "name": "Design Tools", "marketplace": "publisher"}
        entry = self.lib.save_entry({
            "id": entry_id("plugin", "design-tools", {"url": "plugin://design-tools@publisher"}),
            "name": "design-tools", "kind": "plugin", "source": {"url": "plugin://design-tools@publisher"},
            "plugin": plugin, "skills": [{"name": "screen-audit"}, {"name": "draw-diagram"}],
        })
        result = self.lib.install("screen-audit", self.base / "skills")
        self.assertEqual(result["action"], "install_plugin")
        self.assertEqual(result["plugin"]["id"], plugin["id"])
        self.assertFalse((self.base / "skills").exists())
        self.assertEqual(result["entry"]["id"], entry["id"])

    def test_same_named_plugins_from_distinct_marketplaces_coexist_and_children_are_ambiguous(self):
        for market in ("publisher-one", "publisher-two"):
            source = {"kind": "marketplace", "marketplace": market,
                      "reference": "same-plugin@" + market, "original_id": "remote-id"}
            record = {"id": entry_id("plugin", "same-plugin", source),
                      "name": "same-plugin", "kind": "plugin", "source": source,
                      "plugin": {"id": "same-plugin@" + market, "marketplace": market},
                      "skills": [{"name": "shared-child"}]}
            self.lib.save_entry(record)
        self.assertEqual(len(self.lib.entries()), 2)
        self.assertEqual(self.lib.get("same-plugin@publisher-two")["source"]["marketplace"], "publisher-two")
        with self.assertRaises(LibraryError):
            self.lib.get("shared-child")

    def test_plugin_identity_without_reference_retains_marketplace_and_original_id(self):
        first = entry_id("plugin", "same-plugin", {"kind": "marketplace", "marketplace": "first", "original_id": "remote-a"})
        different_market = entry_id("plugin", "same-plugin", {"kind": "marketplace", "marketplace": "second", "original_id": "remote-a"})
        different_origin = entry_id("plugin", "same-plugin", {"kind": "marketplace", "marketplace": "first", "original_id": "remote-b"})
        self.assertNotEqual(first, different_market)
        self.assertNotEqual(first, different_origin)

    def test_traversal_names_and_package_paths_are_rejected(self):
        self.write("SKILL.md", "---\nname: ../../escape\ndescription: Bad\n---\n")
        with self.assertRaises(LibraryError):
            self.lib.publish(self.source, self.meta)
        self.write("SKILL.md", "---\nname: sample-skill\ndescription: Good\n---\n")
        entry = self.lib.publish(self.source, self.meta)
        entry["package"]["path"] = "../outside"
        self.record_path(entry).write_text(json.dumps(entry), encoding="utf-8")
        with self.assertRaises(LibraryError):
            self.lib.install(entry["id"], self.base / "skills")

    def test_secrets_retired_and_removed_packages_are_not_published(self):
        self.write(".env", "SECRET=not-a-real-secret")
        with self.assertRaises(LibraryError):
            self.lib.publish(self.source, self.meta)
        (self.source / ".env").unlink()
        for excluded in ("project-skill-advisor", "muapi-photo-pack-generator"):
            self.write("SKILL.md", "---\nname: " + excluded + "\ndescription: Excluded\n---\n")
            with self.assertRaises(LibraryError):
                self.lib.publish(self.source, self.meta)

    def test_archive_is_reversible_and_prevents_scan_reimport(self):
        entry = self.lib.publish(self.source, self.meta)
        result = self.lib.archive_entry(entry["id"])
        self.assertTrue(Path(result["archive_path"]).is_file())
        self.assertEqual(self.lib.entries(), [])
        self.assertTrue((self.root / entry["package"]["path"]).is_dir())
        with self.assertRaises(LibraryError):
            self.lib.publish(self.source, self.meta)

    def test_linked_supporting_file_cannot_escape_source(self):
        outside = self.base / "private.txt"
        outside.write_text("private", encoding="utf-8")
        try:
            (self.source / "linked.txt").symlink_to(outside)
        except OSError:
            self.skipTest("Platform does not permit test symlinks")
        with self.assertRaises(LibraryError):
            self.lib.publish(self.source, self.meta)

    def test_case_only_file_collisions_are_rejected_before_catalogue_write(self):
        entry = self.lib.publish(self.source, self.meta)
        changed = self.manifest_with_added_path(entry, "skill.md")
        with self.assertRaises(LibraryError):
            self.lib.save_entry(changed, expected=entry)
        self.assertEqual(self.lib.get(entry["id"]), entry)

    def test_case_only_directory_and_file_directory_collisions_are_rejected(self):
        entry = self.lib.publish(self.source, self.meta)
        for bad_path in ("References/second.md", "references", "SCRIPTS/other.py"):
            with self.subTest(path=bad_path):
                changed = self.manifest_with_added_path(entry, bad_path)
                with self.assertRaises(LibraryError):
                    self.lib.save_entry(changed, expected=entry)
                self.assertEqual(self.lib.get(entry["id"]), entry)

    def test_windows_incompatible_manifest_names_are_rejected_on_every_platform(self):
        entry = self.lib.publish(self.source, self.meta)
        for bad_path in (
            "references/CON.txt", "aux", "assets/LPT9.png", "NUL/data.md",
            "references/alternate:stream", "references/back\\slash.md",
            "references/trailing. ", "references/invalid?.md",
        ):
            with self.subTest(path=bad_path):
                changed = self.manifest_with_added_path(entry, bad_path)
                with self.assertRaises(LibraryError):
                    self.lib.save_entry(changed, expected=entry)
                self.assertEqual(self.lib.get(entry["id"]), entry)

    def test_malformed_manifest_values_raise_library_errors_without_writing(self):
        entry = self.lib.publish(self.source, self.meta)
        malformed = [
            [], "bad", {"path": None, "hash": "a" * 64, "files": []},
            {"path": "library/packages/x", "hash": None, "files": [{"path": "SKILL.md", "sha256": "a" * 64}]},
            {"path": "library/packages/x", "hash": "a" * 64, "files": [None]},
            {"path": "library/packages/x", "hash": "a" * 64, "files": [{"path": None, "sha256": "a" * 64}]},
            {"path": "library/packages/x", "hash": "a" * 64, "files": [{"path": "SKILL.md", "sha256": None}]},
        ]
        for bad_package in malformed:
            with self.subTest(package=bad_package):
                changed = deepcopy(entry)
                changed["package"] = bad_package
                with self.assertRaises(LibraryError):
                    self.lib.save_entry(changed, expected=entry)
                self.assertEqual(self.lib.get(entry["id"]), entry)

    def test_nested_environment_secrets_and_linked_directories_are_rejected(self):
        secret = self.write("support/.env.production", "PRIVATE=value")
        with self.assertRaises(LibraryError):
            self.lib.publish(self.source, self.meta)
        secret.unlink()
        outside = self.base / "outside-directory"
        outside.mkdir()
        (outside / "private.txt").write_text("private", encoding="utf-8")
        try:
            (self.source / "support/linked").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Platform does not permit directory symlinks")
        with self.assertRaises(LibraryError):
            self.lib.publish(self.source, self.meta)

    def test_changed_file_manifest_cannot_reuse_an_existing_content_hash(self):
        entry = self.lib.publish(self.source, self.meta)
        changed = deepcopy(entry)
        changed["package"]["files"][0]["sha256"] = "a" * 64
        with self.assertRaises(LibraryError):
            self.lib.save_entry(changed, expected=entry)
        self.assertEqual(self.lib.get(entry["id"]), entry)

    def test_portable_non_ascii_and_spaces_are_preserved(self):
        self.write("references/café notes.md", "ordinary portable document")
        entry = self.lib.publish(self.source, self.meta)
        result = self.lib.install(entry["id"], self.base / "skills")
        self.assertEqual((Path(result["path"]) / "references/café notes.md").read_text(encoding="utf-8"), "ordinary portable document")


if __name__ == "__main__":
    unittest.main()
