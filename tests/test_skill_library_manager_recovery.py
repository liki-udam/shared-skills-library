"""Public manager commands against real packages and simulated publisher HTTP."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / 'skills/shared-skills-library'
sys.path.insert(0, str(MANAGER / 'scripts'))
from skill_library import cli, self_check, transfer, updates
from skill_library.store import Library, fingerprint


def archive(package):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as zipped:
        for path in package.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                zipped.writestr('repo/skills/shared-skills-library/' + path.relative_to(package).as_posix(), path.read_bytes())
    return output.getvalue()


class ManagerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'library'
        self.config = self.base / 'device.json'
        self.skills = self.base / 'device/skills'
        self.library = Library(self.root)
        self.config.write_text(json.dumps({'device_id': 'test', 'name': 'Test', 'platform': 'Windows',
                                          'library_root': str(self.root), 'skills_root': str(self.skills)}))

    def run_cli(self, *args, module=cli, packages=None):
        packages = packages or {'b' * 40: MANAGER}
        out, err = io.StringIO(), io.StringIO()
        def fetch_json(url):
            if '/compare/' in url:
                return {'status': 'ahead'}
            return [{'sha': 'b' * 40}] if '/commits?' in url else {'sha': 'b' * 40}
        def fetch_bytes(url):
            package = packages[url.rsplit('/', 1)[-1]]
            package = package.pop(0) if isinstance(package, list) else package
            return package if isinstance(package, bytes) else archive(package)
        with patch.object(self_check, '_fetch_json', side_effect=fetch_json), patch.object(updates, '_fetch_json', side_effect=fetch_json), patch.object(transfer, '_download_bytes', side_effect=fetch_bytes), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.main(['--library-root', str(self.root), '--config', str(self.config), '--json', *args])
        return code, json.loads(out.getvalue()), err.getvalue()

    def package(self, label, version):
        package = self.base / label
        shutil.copytree(MANAGER, package, ignore=shutil.ignore_patterns('__pycache__'))
        skill = package / 'SKILL.md'
        skill.write_text(re.sub(r'version: "[^"]+"', 'version: "' + version + '"', skill.read_text(encoding='utf-8')), encoding='utf-8')
        return package

    def recovery(self):
        running = self.package('running', '1.0.2')
        old = self.package('old-shared', '1.0.1')
        latest = self.package('publisher', '1.0.3')
        source = dict(self_check.SOURCE, revision='c' * 40, baseline_verified=True)
        self.library.publish(running, {'id': cli.LEGACY_MANAGER_ID, 'source': source})
        self.library.install(cli.LEGACY_MANAGER_ID, self.skills)
        self.library.publish(old, {'id': cli.LEGACY_MANAGER_ID, 'source': dict(source, revision='a' * 40)}, promote=True)
        spec = importlib.util.spec_from_file_location('skill_library.installed_cli', self.skills / 'shared-skills-library/scripts/skill_library/cli.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, {'a' * 40: old, 'b' * 40: latest, 'c' * 40: running}

    def test_unrelated_damaged_record_does_not_prevent_read_only_self_check(self):
        self.library.entry_dir.mkdir(parents=True)
        bad = self.library.entry_dir / 'unrelated.json'
        bad.write_text('{broken')
        before = bad.read_bytes(), self.config.read_bytes()
        code, report, _ = self.run_cli('self-check')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['manager_check']['status'], 'current')
        self.assertEqual((bad.read_bytes(), self.config.read_bytes()), before)
        self.assertFalse((self.library.state / 'devices').exists())

    def test_legitimate_helper_skill_does_not_block_manager_preflight(self):
        helper = self.base / 'helper'
        helper.mkdir()
        (helper / 'SKILL.md').write_text('---\nname: shared-skills-library-helper\ndescription: Unrelated helper\n---\n')
        self.library.publish(helper, {'source': {'kind': 'local', 'origin': 'test'}})
        code, report, _ = self.run_cli('self-check')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['manager_check']['status'], 'current')

    def test_damaged_manager_record_pauses_with_verification_result(self):
        self.library.entry_dir.mkdir(parents=True)
        bad = self.library.entry_dir / (cli.LEGACY_MANAGER_ID + '.json')
        bad.write_text('{broken')
        code, report, _ = self.run_cli('self-check')
        self.assertEqual(code, 4, report)
        self.assertEqual(report['manager_check']['status'], 'verification_failed')
        self.assertTrue(report['paused'])
        self.assertEqual(bad.read_text(), '{broken')
        self.assertFalse((self.library.state / 'devices').exists())

    def test_unrelated_damage_still_blocks_library_operation_after_self_check(self):
        self.library.entry_dir.mkdir(parents=True)
        bad = self.library.entry_dir / 'unrelated.json'
        bad.write_text('{broken')
        self.assertEqual(self.run_cli('self-check')[0], 0)
        code, report, _ = self.run_cli('list')
        self.assertEqual(code, 2)
        self.assertIn('unrelated.json', report['detail'])
        self.assertFalse((self.library.state / 'devices').exists())

    def test_canonical_manager_record_is_read_and_conflicts_are_preserved(self):
        entry = self.library.publish(MANAGER, {'source': dict(self_check.SOURCE, revision='b' * 40, baseline_verified=True)})
        self.assertNotEqual(entry['id'], cli.LEGACY_MANAGER_ID)
        self.assertEqual(self.run_cli('self-check')[0], 0)
        self.library.publish(MANAGER, {'id': cli.LEGACY_MANAGER_ID, 'source': dict(self_check.SOURCE)})
        code, report, _ = self.run_cli('self-check')
        self.assertEqual(code, 4)
        self.assertEqual(report['manager_check']['status'], 'verification_failed')
        self.assertIn('conflicting', report['manager_check']['detail'])

    def test_conflicted_manager_filename_still_pauses_preflight(self):
        self.library.publish(MANAGER, {'id': cli.LEGACY_MANAGER_ID, 'source': dict(self_check.SOURCE)})
        original = self.library.entry_dir / (cli.LEGACY_MANAGER_ID + '.json')
        shutil.copy2(original, self.library.entry_dir / (cli.LEGACY_MANAGER_ID + ' (conflicted copy).json'))
        code, report, _ = self.run_cli('self-check')
        self.assertEqual(code, 4)
        self.assertTrue(report['paused'])

    def test_plain_manager_update_cannot_downgrade_to_older_shared_selection(self):
        module, packages = self.recovery()
        installed = self.skills / 'shared-skills-library'
        original = fingerprint(installed)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        code, report, _ = self.run_cli('update', 'shared-skills-library', module=module, packages=packages)
        self.assertEqual(code, 3, report)
        self.assertTrue(report['paused'])
        self.assertEqual(fingerprint(installed), original)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_current_manager_cannot_install_older_shared_selection(self):
        module, packages = self.recovery()
        packages['b' * 40] = packages['c' * 40]
        installed = self.skills / 'shared-skills-library'
        original = fingerprint(installed)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        code, report, _ = self.run_cli('update', 'shared-skills-library', module=module, packages=packages)
        self.assertEqual(code, 4, report)
        self.assertEqual(report['manager_check']['status'], 'current')
        self.assertTrue(report['paused'])
        self.assertEqual(fingerprint(installed), original)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_explicit_source_update_can_refresh_old_shared_selection_when_running_current(self):
        module, packages = self.recovery()
        packages['b' * 40] = packages['c' * 40]
        installed = self.skills / 'shared-skills-library'
        original = fingerprint(installed)
        code, report, _ = self.run_cli('update', 'shared-skills-library', '--from-source', module=module, packages=packages)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['manager_check']['status'], 'current')
        self.assertEqual(fingerprint(installed), original)
        entry = self.library.get(cli.LEGACY_MANAGER_ID)
        self.assertEqual(entry['package']['hash'], original)
        self.assertEqual(entry['source']['revision'], 'b' * 40)

    def test_authorised_source_update_replaces_real_package_and_verifies_current(self):
        module, packages = self.recovery()
        installed = self.skills / 'shared-skills-library'
        original = fingerprint(installed)
        code, report, _ = self.run_cli('update', 'shared-skills-library', '--from-source', module=module, packages=packages)
        self.assertEqual(code, 0, report)
        self.assertEqual(fingerprint(installed), fingerprint(packages['b' * 40]))
        self.assertEqual(fingerprint(Path(report['result']['backup_path'])), original)
        entry = self.library.get(cli.LEGACY_MANAGER_ID)
        self.assertEqual(entry['source']['revision'], 'b' * 40)
        self.assertTrue(entry['source']['baseline_verified'])
        self.assertEqual(entry['version'], '1.0.3')
        code, report, _ = self.run_cli('self-check', module=module, packages=packages)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['manager_check']['status'], 'current')

    def test_changed_recovery_download_preserves_installed_and_shared_packages(self):
        module, packages = self.recovery()
        installed = self.skills / 'shared-skills-library'
        original = fingerprint(installed)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        packages['b' * 40] = [packages['b' * 40], packages['a' * 40]]
        code, report, _ = self.run_cli('update', 'shared-skills-library', '--from-source', module=module, packages=packages)
        self.assertEqual(code, 4, report)
        self.assertEqual(report['status'], 'verification_failed')
        self.assertTrue(report['paused'])
        self.assertEqual(fingerprint(installed), original)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_damaged_recovery_redownload_returns_paused_verification_failure(self):
        module, packages = self.recovery()
        installed = self.skills / 'shared-skills-library'
        original = fingerprint(installed)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        packages['b' * 40] = [packages['b' * 40], b'not a zip archive']
        code, report, _ = self.run_cli('update', 'shared-skills-library', '--from-source', module=module, packages=packages)
        self.assertEqual(code, 4, report)
        self.assertEqual(report['status'], 'verification_failed')
        self.assertTrue(report['paused'])
        self.assertEqual(fingerprint(installed), original)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
