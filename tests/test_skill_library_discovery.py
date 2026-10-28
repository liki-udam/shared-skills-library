"""Discovery contracts for Shared Skills Library v1.0.0."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "library/skills/shared-skills-library/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def skill(root, name, version=None, description="A useful workflow"):
    folder = root / name
    folder.mkdir(parents=True)
    version_line = "version: " + version + "\n" if version else ""
    (folder / "SKILL.md").write_text(
        "---\nname: " + name + "\n" + version_line + "description: " + description + "\n---\n\nInstructions.\n",
        encoding="utf-8",
    )
    return folder


class DiscoveryTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("skill_library.discovery")
        except ModuleNotFoundError:
            self.fail("The discovery adapter must exist before skills can be inventoried")

    def test_direct_skills_only_and_retired_packages_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill(root, "useful", "1.2.0")
            skill(root, "project-skill-advisor")
            skill(root, "muapi-photo-pack-generator")
            skill(root / "archive", "old-workflow")
            skill(root, ".hidden")
            found = self.module().discover_standalone([root])
            self.assertEqual([entry["name"] for entry in found], ["useful"])
            self.assertEqual(found[0]["version"], "1.2.0")
            self.assertEqual(found[0]["source"]["kind"], "unknown")

    def test_legacy_origin_is_evidence_not_verified_revision_or_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill(root, "useful")
            legacy = {"skills": [{"name": "useful", "version": "8.0.0", "approved_revision": "a" * 40,
                "source": {"repository": "owner/repo", "path": "skills/useful", "ref": "main"},
                "recommendation": {"dependencies": ["Node.js"], "accounts_costs": "Provider credits required"}}]}
            entry = self.module().discover_standalone([root], legacy=legacy)[0]
            self.assertEqual(entry["version"], "unknown")
            self.assertEqual(entry["source"]["url"], "https://github.com/owner/repo")
            self.assertFalse(entry["source"]["baseline_verified"])
            self.assertEqual(entry["source"]["revision"], "a" * 40)
            self.assertEqual(entry["requirements"], ["Node.js"])
            self.assertEqual(entry["accounts_costs"], "Provider credits required")

    def test_npx_lock_retains_original_url_and_tree_hash_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill(root, "useful")
            lock = {"skills": {"useful": {"source": "owner/repo", "sourceType": "github",
                "sourceUrl": "https://github.com/owner/repo.git", "skillPath": "skills/useful/SKILL.md",
                "skillFolderHash": "b" * 40}}}
            entry = self.module().discover_standalone([root], lock=lock)[0]
            self.assertEqual(entry["source"]["original_url"], "https://github.com/owner/repo.git")
            self.assertEqual(entry["source"]["path"], "skills/useful")
            self.assertEqual(entry["source"]["folder_hash"], "b" * 40)
            self.assertFalse(entry["source"]["baseline_verified"])
            self.assertFalse(entry["source"].get("revision"))

    def test_plugin_groups_children_and_keeps_parent_install_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            plugin_root = home / "plugins/cache/local-market/toolkit/2.0.0"
            (plugin_root / ".codex-plugin").mkdir(parents=True)
            (plugin_root / ".codex-plugin/plugin.json").write_text(json.dumps({
                "name": "toolkit", "version": "2.0.0", "skills": "./skills/",
                "interface": {"displayName": "Useful Toolkit"}, "author": {"name": "Publisher"},
            }), encoding="utf-8")
            skill(plugin_root / "skills", "one")
            skill(plugin_root / "skills", "two")
            skill(plugin_root / "skills", "project-skill-advisor")
            skill(home / "plugins/cache/local-market/toolkit/1.0.0/skills", "obsolete")
            inventory = {"installed": [{"pluginId": "toolkit@local-market", "name": "toolkit",
                "marketplaceName": "local-market", "version": "2.0.0", "installed": True,
                "enabled": True, "source": {"source": "remote", "id": "original-plugin-id"}}]}
            found = self.module().discover_plugins(home, inventory)
            self.assertEqual(len(found), 1)
            entry = found[0]
            self.assertEqual(entry["kind"], "plugin")
            self.assertEqual(entry["plugin"]["display_name"], "Useful Toolkit")
            self.assertEqual(entry["plugin"]["install_reference"], "toolkit@local-market")
            self.assertEqual(entry["plugin"]["source"]["id"], "original-plugin-id")
            self.assertEqual([child["name"] for child in entry["skills"]], ["one", "two"])
            self.assertTrue(entry["plugin"]["inventory_verified"])

    def test_cache_alone_never_claims_a_plugin_is_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skill(home / "plugins/cache/a/toolkit/1.0/skills", "one")
            entries = self.module().discover_plugins(home)
            self.assertTrue(all(not entry["plugin"]["installed"] for entry in entries))
            self.assertTrue(all(not entry["plugin"]["inventory_verified"] for entry in entries))

    def test_uninstalled_inventory_entries_are_ignored(self):
        entries = self.module().discover_plugins(Path("missing"), {"installed": [{
            "name": "unused", "marketplaceName": "store", "version": "1", "installed": False}]})
        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
