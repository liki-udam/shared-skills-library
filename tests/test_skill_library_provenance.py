"""Pinned package history and real archive verification, v1.0.3."""
import contextlib
from io import BytesIO, StringIO
from copy import deepcopy
import json
from pathlib import Path
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/shared-skills-library/scripts'))
from skill_library import cli, self_check, transfer
from skill_library.store import Library, fingerprint


def package_text(body='Original', version='1.0.2'):
    return ('---\nname: shared-skills-library\ndescription: Manager\nmetadata:\n'
            '  version: "' + version + '"\n---\n' + body + '\n')


def archive(text, extra=None):
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as output:
        output.writestr('repo-ref/skills/shared-skills-library/SKILL.md', text)
        if extra:
            output.writestr('repo-ref/skills/shared-skills-library/references/guide.md', extra)
    return stream.getvalue()


class ProvenanceTests(unittest.TestCase):
    def test_unsupported_archive_compression_returns_paused_verification_failure(self):
        damaged = bytearray(archive(package_text()))
        struct.pack_into('<H', damaged, damaged.find(b'PK\x03\x04') + 8, 99)
        struct.pack_into('<H', damaged, damaged.find(b'PK\x01\x02') + 10, 99)
        with tempfile.TemporaryDirectory() as directory:
            manager = Path(directory) / 'running'
            manager.mkdir()
            (manager / 'SKILL.md').write_bytes(package_text().encode())
            with patch.object(self_check, '_fetch_json', return_value={'sha': 'b' * 40}), patch.object(transfer, '_download_bytes', return_value=bytes(damaged)):
                result = self_check.check_manager(manager)
            self.assertEqual(result['status'], 'verification_failed')

    def test_malformed_historical_source_returns_verification_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = Path(directory) / 'running'
            manager.mkdir()
            (manager / 'SKILL.md').write_bytes(package_text().encode())
            entry = {'versions': [{'hash': fingerprint(manager), 'source': 'damaged-source-record'}]}
            with patch.object(self_check, '_fetch_json', return_value={'sha': 'b' * 40}), patch.object(transfer, '_download_bytes', return_value=archive(package_text('Publisher fix'))):
                result = self_check.check_manager(manager, entry)
            self.assertEqual(result['status'], 'verification_failed')

    def test_corrupted_supporting_file_is_reported_and_staging_is_removed(self):
        marker = b'Publisher supporting guide'
        damaged = bytearray(archive(package_text(), marker))
        damaged[damaged.find(marker)] ^= 1
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'download'
            with patch.object(transfer, '_download_bytes', return_value=bytes(damaged)):
                with self.assertRaisesRegex(ValueError, 'integrity validation'):
                    transfer.download_github_skill(self_check.SOURCE, 'b' * 40, destination)
            self.assertEqual(list(destination.iterdir()), [])

    def test_known_hash_and_lower_version_alone_do_not_prove_original_source(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = Path(directory) / 'running'
            manager.mkdir()
            (manager / 'SKILL.md').write_bytes(package_text('Unverified', '1.0.1').encode('utf-8'))
            entry = {'source': dict(self_check.SOURCE, revision='b' * 40, baseline_verified=True),
                     'versions': [{'hash': fingerprint(manager), 'version': '1.0.1'}]}
            with patch.object(self_check, '_fetch_json', return_value={'sha': 'b' * 40}), patch.object(transfer, '_download_bytes', return_value=archive(package_text('Publisher fix'))):
                result = self_check.check_manager(manager, entry)
            self.assertEqual(result['status'], 'verification_failed')

    def test_corrupt_publisher_archive_returns_paused_json_without_writes(self):
        text = package_text()
        damaged = bytearray(archive(text))
        offset = damaged.find(text.encode('utf-8'))
        self.assertGreaterEqual(offset, 0)
        damaged[offset + len(text.encode('utf-8')) - 2] ^= 1
        with tempfile.TemporaryDirectory() as directory:
            root, config = Path(directory) / 'library', Path(directory) / 'device.json'
            output = StringIO()
            with patch.object(self_check, '_fetch_json', return_value={'sha': 'b' * 40}), patch.object(transfer, '_download_bytes', return_value=bytes(damaged)), contextlib.redirect_stdout(output):
                code = cli.main(['--library-root', str(root), '--config', str(config), '--json', 'self-check'])
            self.assertEqual(code, 4)
            result = json.loads(output.getvalue())
            self.assertEqual(result['manager_check']['status'], 'verification_failed')
            self.assertTrue(result['paused'])
            self.assertFalse(root.exists())
            self.assertFalse(config.exists())

    def test_historical_same_version_source_verifies_old_running_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = Path(directory) / 'running'
            manager.mkdir()
            (manager / 'SKILL.md').write_bytes(package_text().encode('utf-8'))
            old_hash = fingerprint(manager)
            entry = {'source': dict(self_check.SOURCE, revision='b' * 40, baseline_verified=True),
                     'versions': [{'hash': old_hash, 'source': dict(self_check.SOURCE, revision='a' * 40, baseline_verified=True)}]}
            def download(url):
                return archive(package_text() if '/zip/' + 'a' * 40 in url else package_text('Publisher fix'))
            with patch.object(self_check, '_fetch_json', return_value={'sha': 'b' * 40}), patch.object(transfer, '_download_bytes', side_effect=download):
                result = self_check.check_manager(manager, entry)
            self.assertEqual(result['status'], 'update_available', result)

    def test_same_version_promotion_preserves_each_verified_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            skill = base / 'manager'
            skill.mkdir()
            file = skill / 'SKILL.md'
            library = Library(base / 'library')
            file.write_text(package_text(), encoding='utf-8')
            old = library.publish(skill, {'id': cli.LEGACY_MANAGER_ID,
                'source': dict(self_check.SOURCE, revision='a' * 40, baseline_verified=True)})
            legacy = deepcopy(old)
            legacy['versions'][0].pop('source')
            library.save_entry(legacy, expected=old)
            file.write_text(package_text('Publisher fix'), encoding='utf-8')
            new = library.publish(skill, {'id': cli.LEGACY_MANAGER_ID,
                'source': dict(self_check.SOURCE, revision='b' * 40, baseline_verified=True)}, promote=True)
            by_hash = {version['hash']: version for version in new['versions']}
            self.assertEqual(by_hash[old['package']['hash']]['source']['revision'], 'a' * 40)
            self.assertEqual(by_hash[new['package']['hash']]['source']['revision'], 'b' * 40)


if __name__ == '__main__':
    unittest.main()
