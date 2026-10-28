"""Shared Skills Library v1.0: integration tests without live providers."""
import json
import contextlib
import io
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/shared-skills-library/scripts'))
from skill_library import cli
from skill_library.store import Library, entry_id

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / 'skills/shared-skills-library'
RUNNER = MANAGER / 'scripts/library.py'


class LibraryCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'My Drive' / 'Skills and Plugins'
        self.root.mkdir(parents=True)
        self.config = Path(self.temp.name) / 'local settings' / 'device.json'
        self.skills = Path(self.temp.name) / 'user' / '.agents' / 'skills'

    def run_cli(self, *args):
        return subprocess.run([
            sys.executable, str(RUNNER), '--library-root', str(self.root),
            '--config', str(self.config), '--offline', '--json', *args,
        ], capture_output=True, text=True, timeout=30)

    def setup_device(self):
        result = self.run_cli('setup', '--device-name', 'Test Mac', '--skills-root', str(self.skills))
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_setup_installs_complete_manager_and_records_this_device_only(self):
        result = self.setup_device()
        self.assertEqual(result['result']['status'], 'configured')
        self.assertTrue((self.skills / 'shared-skills-library/SKILL.md').is_file())
        self.assertTrue((self.skills / 'shared-skills-library/scripts/library.py').is_file())
        self.assertTrue(self.config.is_file())
        receipts = list((self.root / 'data/skill-library/devices').glob('*.json'))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        self.assertEqual(receipt['name'], 'Test Mac')
        self.assertIn('last_checked_at', receipt)
        self.assertTrue((self.root / 'Shared Skills Library.md').is_file())

    def test_rerunning_setup_does_not_promote_an_old_executing_manager(self):
        self.setup_device()
        library = Library(self.root)
        original = library.get('shared-skills-library')
        newer_source = Path(self.temp.name) / 'new-manager'
        shutil.copytree(MANAGER, newer_source, ignore=shutil.ignore_patterns('__pycache__'))
        with (newer_source / 'SKILL.md').open('a') as stream:
            stream.write('\nNewer approved manager content.\n')
        metadata = {key: value for key, value in original.items() if key not in {'package', 'versions', 'updated_at'}}
        metadata['version'] = '2.0.0'
        newer = library.publish(newer_source, metadata, promote=True)
        result = self.run_cli('setup', '--device-name', 'Test Mac', '--skills-root', str(self.skills))
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)
        self.assertEqual(library.get('shared-skills-library')['package']['hash'], newer['package']['hash'])
        self.assertEqual(library.get('shared-skills-library')['version'], '2.0.0')

    def test_setup_from_edited_local_copy_does_not_publish_local_edits(self):
        self.setup_device()
        library = Library(self.root)
        original = library.get('shared-skills-library')
        installed = self.skills / 'shared-skills-library'
        with (installed / 'SKILL.md').open('a') as stream:
            stream.write('\nUnpublished local edit.\n')
        result = subprocess.run([sys.executable, str(installed / 'scripts/library.py'), '--library-root', str(self.root), '--config', str(self.config), '--offline', '--json', 'setup'], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(library.get('shared-skills-library')['package']['hash'], original['package']['hash'])

    def test_publish_list_install_and_local_remove_keep_shared_package(self):
        self.setup_device()
        source = Path(self.temp.name) / 'sample'
        (source / 'references').mkdir(parents=True)
        (source / 'SKILL.md').write_text('---\nname: sample\ndescription: A useful sample\nversion: 1.0.0\n---\nRead references/details.md\n')
        (source / 'references/details.md').write_text('Supporting content')
        added = self.run_cli('add', str(source), '--source-url', 'https://github.com/example/skills', '--source-path', 'skills/sample')
        self.assertEqual(added.returncode, 0, added.stderr or added.stdout)
        listing = self.run_cli('list')
        self.assertEqual(listing.returncode, 0, listing.stderr or listing.stdout)
        data = json.loads(listing.stdout)
        self.assertTrue(data['checks'])
        self.assertTrue(all(c['status'] != 'current' for c in data['checks'] if (c.get('source_url') or '').startswith('https://github.com/')))
        installed = self.run_cli('install', 'sample')
        self.assertEqual(installed.returncode, 0, installed.stderr or installed.stdout)
        self.assertEqual((self.skills / 'sample/references/details.md').read_text(), 'Supporting content')
        removed = self.run_cli('remove', 'sample')
        self.assertEqual(removed.returncode, 0, removed.stderr or removed.stdout)
        self.assertFalse((self.skills / 'sample').exists())
        self.assertTrue(json.loads(self.run_cli('show', 'sample').stdout)['result']['package'])

    def test_missing_configuration_is_actionable_and_non_mutating(self):
        result = self.run_cli('install', 'sample')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('setup', (result.stdout + result.stderr).lower())
        self.assertFalse(self.skills.exists())

    def test_unicode_plugin_names_work_with_windows_legacy_output_encoding(self):
        self.setup_device()
        source = {'kind': 'marketplace', 'reference': 'unicode-tools@personal'}
        identity = entry_id('plugin', 'unicode-tools', source)
        Library(self.root).save_entry({'id': identity, 'name': 'unicode-tools', 'kind': 'plugin', 'version': '1.0.0', 'source': source, 'plugin': {'id': 'unicode-tools@personal', 'display_name': '\U0001f510 Unicode Tools', 'version': '1.0.0', 'install_reference': 'unicode-tools@personal'}, 'skills': []})
        result = subprocess.run([sys.executable, str(RUNNER), '--library-root', str(self.root), '--config', str(self.config), '--offline', '--json', 'list'], capture_output=True, env=dict(os.environ, PYTHONIOENCODING='cp1252'), timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace') + result.stdout.decode('utf-8', errors='replace'))
        data = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(next(item for item in data['result'] if item['id'] == identity)['plugin']['display_name'], '\U0001f510 Unicode Tools')

    def test_child_skill_install_routes_whole_plugin_and_checks_fresh_provider(self):
        self.setup_device()
        source = {'kind': 'marketplace', 'reference': 'design-tools@personal', 'latest_version': '1.0.0'}
        plugin = {'id': 'design-tools@personal', 'name': 'design-tools', 'marketplace': 'personal', 'display_name': 'Design Tools', 'version': '1.0.0', 'install_reference': 'design-tools@personal'}
        identity = entry_id('plugin', 'design-tools', source)
        Library(self.root).save_entry({'id': identity, 'name': 'design-tools', 'kind': 'plugin', 'version': '1.0.0', 'source': source, 'plugin': plugin, 'skills': [{'name': 'design-audit'}]})
        inventory = {'installed': [{'pluginId': 'design-tools@personal', 'name': 'design-tools', 'marketplaceName': 'personal', 'version': '1.0.0', 'latestVersion': '2.0.0', 'installed': True, 'source': {'source': 'remote', 'id': 'provider-design-tools'}}]}
        output = io.StringIO()
        with patch.object(cli, 'plugin_inventory', return_value=(inventory, None)), contextlib.redirect_stdout(output):
            code = cli.main(['--library-root', str(self.root), '--config', str(self.config), '--json', 'install', 'design-audit'])
        self.assertEqual(code, 0, output.getvalue())
        data = json.loads(output.getvalue())
        self.assertEqual(data['result']['action'], 'install_plugin')
        self.assertEqual(data['result']['plugin']['install_reference'], 'design-tools@personal')
        self.assertEqual(data['checks'][0]['status'], 'update_available')
        self.assertFalse((self.skills / 'design-audit').exists())
        self.assertIn('From plugin: Design Tools', (self.root / 'Shared Skills Library.md').read_text())

    def test_plugin_check_compares_this_devices_version_not_shared_devices_version(self):
        self.setup_device()
        source = {'kind': 'marketplace', 'reference': 'tools@personal'}
        identity = entry_id('plugin', 'tools', source)
        Library(self.root).save_entry({'id': identity, 'name': 'tools', 'kind': 'plugin', 'version': '2.0.0', 'source': source, 'plugin': {'id': 'tools@personal', 'version': '2.0.0', 'install_reference': 'tools@personal'}, 'skills': []})
        inventory = {'installed': [{'pluginId': 'tools@personal', 'name': 'tools', 'marketplaceName': 'personal', 'version': '1.0.0', 'latestVersion': '2.0.0', 'installed': True, 'source': {'source': 'remote', 'id': 'provider-tools'}}]}
        output = io.StringIO()
        with patch.object(cli, 'plugin_inventory', return_value=(inventory, None)), contextlib.redirect_stdout(output):
            code = cli.main(['--library-root', str(self.root), '--config', str(self.config), '--json', 'show', 'tools'])
        self.assertEqual(code, 0, output.getvalue())
        check = json.loads(output.getvalue())['checks'][0]
        self.assertEqual(check['status'], 'update_available')
        self.assertEqual(check['current_revision'], '1.0.0')
        self.assertEqual(check['library_revision'], '2.0.0')

    def test_failed_fresh_provider_check_does_not_reuse_cached_current_status(self):
        self.setup_device()
        source = {'kind': 'marketplace', 'reference': 'tools@personal', 'latest_version': '1.0.0'}
        identity = entry_id('plugin', 'tools', source)
        Library(self.root).save_entry({'id': identity, 'name': 'tools', 'kind': 'plugin', 'version': '1.0.0', 'source': source, 'plugin': {'id': 'tools@personal', 'version': '1.0.0', 'install_reference': 'tools@personal'}, 'skills': []})
        output = io.StringIO()
        with patch.object(cli, 'plugin_inventory', return_value=(None, 'Provider cannot be reached')), contextlib.redirect_stdout(output):
            code = cli.main(['--library-root', str(self.root), '--config', str(self.config), '--json', 'show', 'tools'])
        self.assertEqual(code, 0, output.getvalue())
        self.assertNotEqual(json.loads(output.getvalue())['checks'][0]['status'], 'current')

    def test_removing_another_publishers_same_name_does_not_touch_local_copy(self):
        self.setup_device()
        library = Library(self.root)
        sources = []
        for author in ('publisher-a', 'publisher-b'):
            directory = Path(self.temp.name) / author
            directory.mkdir()
            (directory / 'SKILL.md').write_text('---\nname: same-name\ndescription: Example\n---\n' + author)
            sources.append(library.publish(directory, {'source': {'kind': 'github', 'repository': author + '/skills', 'path': 'same-name'}}))
        library.install(sources[0]['id'], self.skills)
        result = self.run_cli('remove', sources[1]['id'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('publisher-a', (self.skills / 'same-name/SKILL.md').read_text())

    def test_update_and_remove_target_a_detected_codex_installation(self):
        self.setup_device()
        library = Library(self.root)
        source = Path(self.temp.name) / 'sample-source'
        source.mkdir()
        (source / 'SKILL.md').write_text('---\nname: elsewhere\ndescription: Example\nversion: 1.0.0\n---\nOld content')
        original = library.publish(source, {'source': {'kind': 'local', 'origin': 'test'}})
        elsewhere = Path(self.temp.name) / 'user/.codex/skills'
        library.install(original['id'], elsewhere)
        settings = json.loads(self.config.read_text())
        settings['scan_roots'] = [str(self.skills), str(elsewhere)]
        self.config.write_text(json.dumps(settings))
        (source / 'SKILL.md').write_text('---\nname: elsewhere\ndescription: Example\nversion: 2.0.0\n---\nNew content')
        library.publish(source, {'source': {'kind': 'local', 'origin': 'test'}}, promote=True)
        updated = self.run_cli('update', original['id'])
        self.assertEqual(updated.returncode, 0, updated.stdout or updated.stderr)
        self.assertIn('New content', (elsewhere / 'elsewhere/SKILL.md').read_text())
        self.assertFalse((self.skills / 'elsewhere').exists())
        removed = self.run_cli('remove', original['id'])
        self.assertEqual(removed.returncode, 0, removed.stdout or removed.stderr)
        self.assertFalse((elsewhere / 'elsewhere').exists())


if __name__ == '__main__':
    unittest.main()
