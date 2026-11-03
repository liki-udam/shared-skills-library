"""Manager preflight ordering, source comparison and recovery, v1.0.2."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/shared-skills-library/scripts'))
from skill_library import cli, self_check
from skill_library.store import Library, fingerprint, read_skill


class ManagerCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manager = self.base / 'running'
        self.latest = self.base / 'publisher'
        for path in (self.manager, self.latest):
            path.mkdir()
            (path / 'SKILL.md').write_text('---\nname: shared-skills-library\ndescription: Manager\nmetadata:\n  version: "1.0.2"\n---\nSame content\n')
        self.revision = patch.object(self_check, 'latest_revision', return_value='b' * 40)
        self.revision.start()
        self.addCleanup(self.revision.stop)

    def entry(self):
        return {'source': dict(self_check.SOURCE, revision='a' * 40, baseline_verified=True),
                'versions': [{'hash': fingerprint(self.manager),
                              'source': dict(self_check.SOURCE, revision='a' * 40, baseline_verified=True)}]}

    def check(self, entry=None, baseline=None):
        def download(source, revision, destination):
            return baseline if revision == 'a' * 40 else self.latest
        with patch.object(self_check, 'download_github_skill', side_effect=download):
            return self_check.check_manager(self.manager, entry)

    def test_matching_bytes_are_current_without_library_or_version_receipt(self):
        self.assertEqual(self.check()['status'], 'current')
        self.assertEqual(read_skill(self.manager)['version'], '1.0.2')

    def test_unrelated_repo_commit_does_not_mean_package_update(self):
        self.assertEqual(self.check(self.entry())['status'], 'current')

    def test_verified_running_baseline_reports_newer_package_even_same_version(self):
        baseline = self.base / 'baseline'
        shutil.copytree(self.manager, baseline)
        (self.latest / 'fix.txt').write_text('Publisher fix')
        self.assertEqual(self.check(self.entry(), baseline)['status'], 'update_available')

    def test_locally_modified_copy_is_unverified_not_current_or_update(self):
        baseline = self.base / 'baseline'
        shutil.copytree(self.manager, baseline)
        entry = self.entry()
        (self.manager / 'device-only.txt').write_text('Local edit')
        self.assertEqual(self.check(entry, baseline)['status'], 'verification_failed')

    def test_newer_shared_record_does_not_hide_old_executing_version(self):
        text = (self.manager / 'SKILL.md').read_text().replace('1.0.2', '1.0.1')
        (self.manager / 'SKILL.md').write_text(text)
        entry = self.entry()
        entry['source']['revision'] = 'b' * 40
        entry['package'] = {'hash': fingerprint(self.latest)}
        self.assertEqual(self.check(entry, self.manager)['status'], 'update_available')

    def test_offline_never_contacts_publisher(self):
        with patch.object(self_check, 'latest_revision') as fetch:
            self.assertEqual(self_check.check_manager(self.manager, offline=True)['status'], 'verification_failed')
            fetch.assert_not_called()

    def test_download_and_network_failures_pause(self):
        with patch.object(self_check, 'download_github_skill', side_effect=OSError('offline')):
            self.assertEqual(self_check.check_manager(self.manager)['status'], 'verification_failed')


class RevisionDiscoveryTests(unittest.TestCase):
    def test_api_success_avoids_git(self):
        with patch.object(self_check, '_fetch_json', return_value={'sha': 'b' * 40}), patch.object(self_check.subprocess, 'run') as git:
            self.assertEqual(self_check.latest_revision(), 'b' * 40)
            git.assert_not_called()

    def test_api_rate_limit_falls_back_to_git(self):
        with patch.object(self_check, '_fetch_json', side_effect=HTTPError('https://api.github.com', 403, 'rate limited', {}, None)), patch.object(self_check.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'b' * 40 + '\trefs/heads/main\n', '')) as git:
            self.assertEqual(self_check.latest_revision(), 'b' * 40)
            self.assertEqual(git.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'], '0')

    def test_complete_discovery_failure_is_not_current(self):
        with patch.object(self_check, '_fetch_json', side_effect=OSError()), patch.object(self_check.subprocess, 'run', side_effect=OSError()):
            with tempfile.TemporaryDirectory() as directory:
                (Path(directory) / 'SKILL.md').write_text('Example')
                self.assertEqual(self_check.check_manager(Path(directory))['status'], 'verification_failed')


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'library'
        self.config = Path(self.temp.name) / 'device.json'

    def run_cli(self, *args, status='current'):
        out, err = io.StringIO(), io.StringIO()
        check = {'status': status, 'detail': 'Manager check: ' + status, 'latest_revision': 'b' * 40}
        with patch.object(cli, 'check_manager', return_value=check), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['--library-root', str(self.root), '--config', str(self.config), *args])
        return code, out.getvalue(), err.getvalue()

    def test_all_operational_commands_stop_before_state_changes(self):
        commands = [('setup',), ('list',), ('search', 'x'), ('show', 'x'), ('scan',), ('add', str(self.root)), ('install', 'x'), ('update', 'x'), ('remove', 'x'), ('verify',), ('status',)]
        for status, expected in [('update_available', 3), ('verification_failed', 4)]:
            for command in commands:
                with self.subTest(status=status, command=command):
                    code, output, _ = self.run_cli('--json', *command, status=status)
                    self.assertEqual(code, expected)
                    self.assertTrue(json.loads(output)['paused'])
                    self.assertFalse(self.root.exists())
                    self.assertFalse(self.config.exists())

    def test_self_check_is_read_only_silent_when_current_and_machine_readable(self):
        self.assertEqual(self.run_cli('self-check'), (0, '', ''))
        for status, expected in [('current', 0), ('update_available', 3), ('verification_failed', 4)]:
            code, output, _ = self.run_cli('--json', 'self-check', status=status)
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(output)['manager_check']['status'], status)
        self.assertFalse(self.root.exists())

    def test_current_continues_to_operation_without_notification(self):
        with patch.object(cli, 'render_catalogue'), patch.object(cli, 'save_receipt'), patch.object(cli, 'refresh_checks', return_value=[]):
            code, out, err = self.run_cli('list')
        self.assertEqual((code, out, err), (0, '', ''))

    def test_paused_commands_preserve_existing_catalogue_settings_and_receipts(self):
        library = Library(self.root)
        library.publish(ROOT / 'skills/shared-skills-library',
                        {'id': cli.LEGACY_MANAGER_ID, 'source': dict(self_check.SOURCE)})
        self.config.write_text(json.dumps({'device_id': 'test', 'skills_root': str(self.root / 'skills')}))
        receipt = self.root / 'data/skill-library/devices/test.json'
        receipt.parent.mkdir(parents=True)
        receipt.write_text('{"last_checked_at":"original"}')
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        config_before = self.config.read_bytes()
        for command in [('scan',), ('update', 'unknown-package'), ('setup',)]:
            self.assertEqual(self.run_cli('--json', *command, status='update_available')[0], 3)
        self.assertEqual(self.run_cli('--json', 'update', 'shared-skills-library', '--from-source', status='verification_failed')[0], 4)
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(config_before, self.config.read_bytes())

    def test_explicit_manager_update_can_clear_pending_update(self):
        source = ROOT / 'skills/shared-skills-library'
        library = Library(self.root)
        entry = library.publish(source, {'id': cli.LEGACY_MANAGER_ID, 'source': dict(self_check.SOURCE), 'version': cli.VERSION})
        self.config.write_text(json.dumps({'device_id': 'test', 'skills_root': str(self.root / 'skills')}))
        with patch.object(cli, 'update_from_source', return_value={'status': 'installed'}) as update, patch.object(cli, 'refresh_checks', return_value=[]), patch.object(cli, 'save_receipt'), patch.object(cli, 'render_catalogue'):
            code, output, _ = self.run_cli('--json', 'update', entry['id'], '--from-source', status='update_available')
        self.assertEqual(code, 0, output)
        update.assert_called_once()
        self.assertEqual(update.call_args.args[3]['latest_revision'], 'b' * 40)

    def test_actual_offline_command_and_help_need_no_network(self):
        runner = ROOT / 'skills/shared-skills-library/scripts/library.py'
        result = subprocess.run([sys.executable, str(runner), '--library-root', str(self.root), '--config', str(self.config), '--offline', '--json', 'setup'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 4)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.config.exists())
        help_result = subprocess.run([sys.executable, str(runner), '--help'], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
