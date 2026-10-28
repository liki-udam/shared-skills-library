"""Shared Skills Library v1.0.1: downloaded setup without private device state."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / 'skills/shared-skills-library'
if not MANAGER.is_dir():
    MANAGER = ROOT / 'skills/shared-skills-library'
sys.path.insert(0, str(MANAGER / 'scripts'))
from skill_library import cli
from skill_library.store import Library, fingerprint

LEGACY_ID = 'standalone-shared-skills-library-cc8fa642dee9'
SOURCE = {'kind': 'github', 'repository': 'liki-udam/shared-skills-library',
          'url': 'https://github.com/liki-udam/shared-skills-library',
          'path': 'skills/shared-skills-library', 'ref': 'main', 'baseline_verified': False}


class DownloadedSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'My Drive/Skills and Plugins'
        self.master = self.root / 'library/skills/shared-skills-library'
        shutil.copytree(MANAGER, self.master, ignore=shutil.ignore_patterns('__pycache__'))
        self.codex = self.base / 'test user/.codex'
        self.download = self.codex / 'skills/shared-skills-library'
        shutil.copytree(MANAGER, self.download, ignore=shutil.ignore_patterns('__pycache__'))
        self.config = self.codex / 'shared-skills-library/device.json'
        self.library = Library(self.root)
        self.entry = self.library.publish(self.master, {'id': LEGACY_ID, 'source': SOURCE, 'version': cli.VERSION})

    def bootstrap(self, *args, skill=None):
        return subprocess.run([sys.executable, str((skill or self.download) / 'scripts/bootstrap.py'),
                               '--config', str(self.config), '--offline', '--json', *map(str, args)],
                              capture_output=True, text=True, encoding='utf-8', timeout=30,
                              env=dict(os.environ, CODEX_HOME=str(self.codex)))

    def test_downloaded_copy_pairs_to_drive_without_duplicate_install(self):
        result = self.bootstrap('--library-root', self.root, '--device-name', 'Second Windows')
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        settings = json.loads(self.config.read_text(encoding='utf-8'))
        self.assertEqual(Path(settings['skills_root']), self.download.parent)
        self.assertEqual(fingerprint(self.download), self.entry['package']['hash'])
        self.assertEqual(len(self.library.entries()), 1)
        self.assertEqual(self.library.get('shared-skills-library')['id'], LEGACY_ID)
        self.assertEqual(len(list((self.root / 'data/skill-library/devices').glob('*.json'))), 1)

    def test_repeated_downloaded_setup_uses_saved_root_and_device_identity(self):
        first = self.bootstrap('--library-root', self.root)
        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        settings = json.loads(self.config.read_text(encoding='utf-8'))
        second = self.bootstrap()
        self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['device_id'], settings['device_id'])
        self.assertEqual(len(list((self.root / 'data/skill-library/devices').glob('*.json'))), 1)

    def test_shared_master_still_infers_root(self):
        result = self.bootstrap('--skills-root', self.codex / 'skills', skill=self.master)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(Path(json.loads(self.config.read_text(encoding='utf-8'))['library_root']), self.root)

    def test_missing_or_unrecognised_drive_location_does_not_create_library(self):
        for target in (self.base / 'missing drive', self.base / 'ordinary folder'):
            if target.name == 'ordinary folder':
                target.mkdir()
            result = self.bootstrap('--library-root', target)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((target / 'data').exists())
            self.assertFalse(self.config.exists())

    def test_unconfigured_download_requires_drive_location(self):
        result = self.bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--library-root', result.stderr)
        self.assertFalse(self.config.exists())

    def test_existing_pairing_cannot_be_changed_by_setup(self):
        first = self.bootstrap('--library-root', self.root)
        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        before = self.config.read_bytes()
        other = self.base / 'Other Drive'
        (other / 'data/skill-library/entries').mkdir(parents=True)
        result = self.bootstrap('--library-root', other)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(list((other / 'data/skill-library/entries').iterdir()), [])

    def test_repeat_setup_does_not_downgrade_a_known_newer_local_manager(self):
        result = self.bootstrap('--library-root', self.root)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        newer = self.base / 'newer-manager'
        shutil.copytree(self.master, newer)
        skill = newer / 'SKILL.md'
        text = skill.read_text(encoding='utf-8')
        skill.write_text(text.replace('version: "1.0.1"', 'version: "2.0.0"'), encoding='utf-8')
        updated = self.library.publish(newer, {'id': LEGACY_ID, 'source': SOURCE, 'version': '2.0.0'}, promote=True)
        self.library.install(LEGACY_ID, self.download.parent)
        reverted = self.library.publish(self.master, {'id': LEGACY_ID, 'source': SOURCE, 'version': cli.VERSION}, promote=True)
        self.assertEqual(updated['version'], '2.0.0')
        self.assertEqual(reverted['version'], cli.VERSION)
        settings_before = self.config.read_bytes()
        result = self.bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fingerprint(self.download), updated['package']['hash'])
        self.assertEqual(self.config.read_bytes(), settings_before)

    def test_downloaded_local_edits_are_preserved(self):
        path = self.download / 'SKILL.md'
        path.write_text(path.read_text(encoding='utf-8') + '\nUnpublished local edit.\n', encoding='utf-8')
        before = fingerprint(self.download)
        result = self.bootstrap('--library-root', self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fingerprint(self.download), before)
        self.assertEqual(self.library.get('shared-skills-library')['package']['hash'], self.entry['package']['hash'])
        self.assertFalse(self.config.exists())

    def test_newer_download_does_not_replace_the_shared_selection(self):
        path = self.master / 'SKILL.md'
        path.write_text(path.read_text(encoding='utf-8') + '\nDifferent selected shared package.\n', encoding='utf-8')
        newer = self.library.publish(self.master, {'id': LEGACY_ID, 'source': SOURCE}, promote=True)
        before = fingerprint(self.download)
        result = self.bootstrap('--library-root', self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fingerprint(self.download), before)
        self.assertEqual(self.library.get('shared-skills-library')['package']['hash'], newer['package']['hash'])

    def test_scan_does_not_duplicate_migrated_manager_identity(self):
        legacy = {'skills': [{'name': 'shared-skills-library', 'source': {
            'kind': 'custom', 'origin': 'WF Designz Google Drive Shared Skills Library'}}]}
        (self.root / 'data/shared-skills.json').write_text(json.dumps(legacy), encoding='utf-8')
        settings = {'device_id': 'test-device', 'name': 'Test', 'platform': 'Windows',
                    'library_root': str(self.root), 'skills_root': str(self.download.parent),
                    'scan_roots': [str(self.download.parent)]}
        with patch.object(cli, 'plugin_inventory', return_value=({'installed': []}, None)):
            result = cli.scan_device(self.library, settings, include_masters=True)
        self.assertEqual(result['issues'], [])
        self.assertEqual(len(self.library.entries()), 1)
        self.assertEqual(self.library.get('shared-skills-library')['source'], SOURCE)

    def test_scan_preserves_old_or_edited_manager_as_variant_under_existing_identity(self):
        # Fresh devices can have no legacy source record at all.
        changed = self.download / 'SKILL.md'
        changed.write_text(changed.read_text(encoding='utf-8') + '\nDevice-only manager edit.\n', encoding='utf-8')
        settings = {'device_id': 'test-device', 'name': 'Test', 'platform': 'Windows',
                    'library_root': str(self.root), 'skills_root': str(self.download.parent),
                    'scan_roots': [str(self.download.parent)]}
        with patch.object(cli, 'plugin_inventory', return_value=({'installed': []}, None)):
            result = cli.scan_device(self.library, settings)
        self.assertEqual(len(self.library.entries()), 1)
        self.assertEqual(self.library.get('shared-skills-library')['package']['hash'], self.entry['package']['hash'])
        self.assertTrue(any('not promoted' in issue['detail'] for issue in result['issues']))


if __name__ == '__main__':
    unittest.main()
