"""Shared Skills Library v1.0.1: commands, dated device receipts and catalogue."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import uuid
from urllib.parse import urlsplit

from .discovery import discover_plugins, discover_standalone
from .store import Library, LibraryError, entry_id, fingerprint, read_skill
from .updates import check_all

VERSION = '1.0.1'
MANAGER_NAME = 'shared-skills-library'
MANAGER_REPOSITORY = 'liki-udam/shared-skills-library'
MANAGER_SOURCE_PATH = 'skills/shared-skills-library'
LEGACY_MANAGER_ID = 'standalone-shared-skills-library-cc8fa642dee9'


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def codex_home():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError) as error:
        raise LibraryError('Cannot read ' + str(path) + ': ' + str(error)) from error


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def plugin_inventory(available=False):
    executable = shutil.which('codex')
    if not executable:
        return None, 'Codex plugin inventory is unavailable on this device.'
    try:
        command = [executable, 'plugin', 'list', '--json']
        if available:
            command.append('--available')
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=40)
        if result.returncode:
            return None, 'Codex could not verify the installed plugin inventory.'
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or not isinstance(data.get('installed'), list):
            return None, 'Codex returned an unsupported plugin inventory format.'
        return data, None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, 'Codex plugin inventory could not be read; cached files are not proof of installation.'


def public_source(url, path='.', ref='main', revision=None):
    if not url:
        return {'kind': 'unknown', 'baseline_verified': False}
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise LibraryError('Use the original public HTTPS source URL without credentials or signed query parameters.')
    clean = parts._replace(query='', fragment='').geturl()
    source = {'kind': 'url', 'url': clean, 'original_url': clean, 'path': path, 'baseline_verified': False}
    if parts.hostname.lower() == 'github.com':
        segments = parts.path.strip('/').removesuffix('.git').split('/')
        if len(segments) != 2 or not all(re.fullmatch(r'[\w.-]+', segment) for segment in segments):
            raise LibraryError('Use the GitHub repository URL and specify its skill folder with --source-path.')
        source.update(kind='github', repository='/'.join(segments), ref=ref, tracking='branch')
    if revision:
        if not re.fullmatch(r'[0-9a-fA-F]{40}', revision):
            raise LibraryError('An original-source revision must be a full 40-character Git commit.')
        source['revision'] = revision
        source['evidence'] = 'Revision supplied during publication; content baseline not yet independently verified.'
    return source


def _local_status(entry, settings):
    if entry['kind'] == 'plugin':
        if not settings.get('device_id'):
            return {'status': 'device_not_configured'}
        receipt = read_json(Path(settings['library_root']) / 'data/skill-library/devices' / (settings['device_id'] + '.json'), {})
        return receipt.get('installations', {}).get(entry['id'], {'status': 'plugin_inventory_required'})
    roots = [settings.get('skills_root')] + settings.get('scan_roots', [])
    observations = []
    seen = set()
    for root in roots:
        if not root:
            continue
        target = Path(root).expanduser() / entry['name']
        if not target.is_dir() or str(target.resolve()) in seen:
            continue
        seen.add(str(target.resolve()))
        try:
            actual = fingerprint(target)
            shared = entry.get('package', {}).get('hash')
            known = {item.get('hash') for item in entry.get('versions', [])}
            status = 'current' if actual == shared else 'shared_update_available' if actual in known else 'locally_modified_or_different_source'
            version = entry.get('version') if actual == shared else next((item.get('version') for item in entry.get('versions', []) if item.get('hash') == actual), 'unknown')
            observations.append({'scope': Path(root).parent.name + '/' + Path(root).name, 'hash': actual, 'version': version, 'status': status})
        except (LibraryError, OSError) as error:
            observations.append({'scope': Path(root).parent.name + '/' + Path(root).name, 'status': 'cannot_verify', 'detail': str(error)})
    return {'status': 'not_installed', 'copies': []} if not observations else {'status': observations[0]['status'] if len(observations) == 1 else 'multiple_local_copies', 'copies': observations}


def save_receipt(library, settings, plugin_entries=None):
    if not settings.get('device_id'):
        return
    destination = library.state / 'devices' / (settings['device_id'] + '.json')
    previous = read_json(destination, {})
    installations = previous.get('installations', {})
    stamp = now()
    for entry in library.entries():
        if entry['kind'] == 'standalone':
            installations[entry['id']] = dict(_local_status(entry, settings), checked_at=stamp)
    if plugin_entries is not None:
        live_ids = set()
        for entry in plugin_entries:
            identity = entry_id('plugin', entry['name'], entry['source'])
            live_ids.add(identity)
            installations[identity] = {'status': 'installed', 'version': entry['version'], 'enabled': entry['plugin'].get('enabled'), 'checked_at': stamp, 'kind': 'plugin'}
        for identity, observation in installations.items():
            if observation.get('kind') == 'plugin' and identity not in live_ids:
                installations[identity] = dict(observation, status='not_installed', checked_at=stamp)
    receipt = {'schema_version': 1, 'device_id': settings['device_id'], 'name': settings['name'], 'platform': settings['platform'], 'last_checked_at': stamp, 'installations': installations}
    write_json(destination, receipt)


def refresh_checks(library, entries, settings, offline=False):
    if offline:
        checks = [{'id': entry['id'], 'status': 'check_incomplete_offline', 'checked_at': now(), 'detail': 'Online source checks were explicitly skipped; no up-to-date claim is made.', 'source_url': entry.get('source', {}).get('url')} for entry in entries]
    else:
        checked_entries = deepcopy(entries)
        library_versions = {entry['id']: entry.get('plugin', {}).get('version') or entry.get('version') for entry in entries if entry['kind'] == 'plugin'}
        provider_issue = None
        if any(entry['kind'] == 'plugin' for entry in checked_entries):
            inventory, issue = plugin_inventory(available=True)
            provider_issue = issue
            observed = discover_plugins(codex_home(), inventory) if inventory is not None else []
            live = {entry['plugin']['id']: entry for entry in observed}
            available = {item.get('pluginId'): item for item in (inventory or {}).get('available', [])}
            for entry in checked_entries:
                if entry['kind'] != 'plugin':
                    continue
                # A previous observation cannot establish the provider's current version.
                entry['source'].pop('latest_version', None)
                entry.get('plugin', {}).pop('latest_version', None)
                identity = entry.get('plugin', {}).get('id')
                local = live.get(identity)
                if inventory is not None:
                    local_version = local['version'] if local else 'not_installed'
                    entry['plugin']['version'] = local_version
                    entry['version'] = local_version
                latest = live.get(identity, {}).get('source', {}).get('latest_version')
                if not latest and identity in available:
                    latest = available[identity].get('latestVersion') or available[identity].get('latest_version') or available[identity].get('version')
                if latest:
                    entry['source']['latest_version'] = latest
            if inventory is not None:
                save_receipt(library, settings, observed)
        checks = check_all(checked_entries)
        for check in checks:
            if check['id'] in library_versions:
                check['library_revision'] = library_versions[check['id']]
                check['comparison'] = 'this_device_against_original_provider'
                if provider_issue:
                    check.update(status='check_failed', detail=provider_issue)
                elif check.get('current_revision') == 'not_installed':
                    check.update(status='not_installed', detail='This plugin is not installed on this device; install the whole parent through its original provider.')
    if settings.get('device_id'):
        destination = library.state / 'checks' / (settings['device_id'] + '.json')
        previous = read_json(destination, {'checks': []})
        merged = {item['id']: item for item in previous.get('checks', [])}
        merged.update({item['id']: item for item in checks})
        write_json(destination, {'device_id': settings['device_id'], 'checked_at': now(), 'checks': list(merged.values())})
    return checks


def _cell(value):
    return str(value if value is not None else 'Unknown').replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')


def render_catalogue(library):
    entries = library.entries()
    reports = {}
    for path in sorted((library.state / 'checks').glob('*.json')):
        for item in read_json(path, {}).get('checks', []):
            old = reports.get(item['id'])
            if not old or item.get('checked_at', '') > old.get('checked_at', ''):
                reports[item['id']] = item
    lines = ['# Shared Skills Library', '', 'Version 1.0.0. Generated: ' + now(), '',
             'Google Drive holds complete standalone packages and original-source records. Each device keeps its own working copies. Plugin entries install the whole parent plugin.', '',
             'Source checks report availability; they do not apply updates. Missing baselines, provider-managed versions and failed checks remain explicit. File integrity does not prove external software or account access is ready.', '',
             '| Name | Type | Version | Original source | Update check |', '|---|---|---|---|---|']
    for entry in sorted(entries, key=lambda item: (item.get('plugin', {}).get('display_name') or item['name']).casefold()):
        source = entry.get('source', {})
        parent = entry.get('plugin', {})
        label = 'From plugin: ' + parent.get('display_name', entry['name']) if entry['kind'] == 'plugin' else 'Standalone skill'
        origin = source.get('original_url') or source.get('url') or source.get('reference') or source.get('origin') or 'Unknown'
        report = reports.get(entry['id'], {})
        check = report.get('status', 'not_checked') + (' (' + report['checked_at'] + ')' if report.get('checked_at') else '')
        version = entry.get('version', 'unknown')
        if version == 'unknown' and entry.get('package'):
            version = 'Unversioned; package ' + entry['package']['hash'][:12]
        lines.append('| ' + ' | '.join(map(_cell, [parent.get('display_name') or entry['name'], label, version, origin, check])) + ' |')
    lines.extend(['', '## Device observations', '', 'These are dated observations from each device, not live remote checks.', '', '| Device | Platform | Last checked | Observed installations |', '|---|---|---|---|'])
    receipts = [read_json(path, {}) for path in sorted((library.state / 'devices').glob('*.json'))]
    for receipt in receipts:
        count = sum(item.get('status') != 'not_installed' for item in receipt.get('installations', {}).values())
        lines.append('| ' + ' | '.join(map(_cell, [receipt.get('name'), receipt.get('platform'), receipt.get('last_checked_at'), count])) + ' |')
    windows = sum(receipt.get('platform') == 'Windows' for receipt in receipts)
    macs = sum(receipt.get('platform') in {'Darwin', 'macOS'} for receipt in receipts)
    lines.extend(['', 'Expected devices: two Windows computers and one Mac. Pending verified setup: ' + str(max(0, 2 - windows)) + ' Windows, ' + str(max(0, 1 - macs)) + ' Mac.', '', '## Entry details', ''])
    for entry in sorted(entries, key=lambda item: item['id']):
        lines.extend(['### ' + _cell(entry.get('plugin', {}).get('display_name') or entry['name']), '',
                      '- ID: `' + entry['id'] + '`', '- Purpose: ' + _cell(entry.get('description') or 'Not recorded.'),
                      '- Publisher/author: ' + _cell(entry.get('author') or 'Unknown'),
                      '- Requirements: ' + _cell('; '.join(entry.get('requirements') or []) or 'Not independently verified.'),
                      '- Accounts/costs: ' + _cell(entry.get('accounts_costs') or 'Unknown'),
                      '- Platforms: ' + _cell('; '.join(entry.get('platforms') or []) or 'Not verified'),
                      '- Source evidence: ' + _cell(entry.get('source', {}).get('evidence') or 'See original source record.'),
                      '- Licence: ' + _cell(entry.get('license') or 'Unknown')])
        original = entry.get('source', {})
        check_detail = reports.get(entry['id'])
        if check_detail:
            lines.append('- Last source check: ' + _cell(check_detail.get('status')) + '; ' + _cell(check_detail.get('detail')))
            if check_detail.get('latest_revision'):
                lines.append('- Latest original-source version/revision observed: `' + _cell(check_detail['latest_revision']) + '`.')
        if original.get('path'):
            lines.append('- Original package folder: `' + _cell(original['path']) + '`; tracked branch/ref: `' + _cell(original.get('ref') or 'Not recorded') + '`.')
        if original.get('revision'):
            lines.append('- Recorded original revision: `' + _cell(original['revision']) + '`; installed baseline verified: ' + str(original.get('baseline_verified') is True) + '.')
        if entry['kind'] == 'plugin':
            lines.extend(['- Type: **From plugin: ' + _cell(entry['plugin'].get('display_name') or entry['name']) + '**', '- Whole-plugin installation: `' + entry['plugin'].get('install_reference', entry['name']) + '`', '- Bundled skills: ' + ', '.join(_cell(child['name']) for child in entry.get('skills', []))])
        else:
            lines.append('- Complete package: `' + entry.get('package', {}).get('path', 'not_available') + '`')
        for receipt in receipts:
            observation = receipt.get('installations', {}).get(entry['id'])
            if observation:
                versions = ', '.join(str(item.get('version', 'unknown')) for item in observation.get('copies', [])) or observation.get('version', 'not recorded')
                lines.append('- Device ' + _cell(receipt.get('name')) + ': ' + _cell(observation.get('status')) + '; version ' + _cell(versions) + '; checked ' + _cell(observation.get('checked_at')) + '.')
        lines.append('')
    target = library.root / 'Shared Skills Library.md'
    temporary = target.with_name('.shared-skills-catalogue-' + uuid.uuid4().hex + '.tmp')
    temporary.write_text('\n'.join(lines), encoding='utf-8')
    os.replace(temporary, target)


def metadata_for_source(directory, args):
    details = read_skill(directory)
    return {'name': details['name'], 'kind': 'standalone', 'description': details['description'],
            'version': getattr(args, 'version', None) or details['version'],
            'source': public_source(getattr(args, 'source_url', None), getattr(args, 'source_path', '.') or '.', getattr(args, 'source_ref', 'main') or 'main', getattr(args, 'source_revision', None)),
            'requirements': [], 'accounts_costs': 'Unknown; inspect the original source and supporting files.', 'platforms': ['Not independently verified']}


def manager_entry(library):
    """Keep the original manager identity after moving its distribution to GitHub."""
    named = [entry for entry in library.entries() if entry['kind'] == 'standalone' and entry['name'] == MANAGER_NAME]
    matching = [entry for entry in named if entry['id'] == LEGACY_MANAGER_ID or
                (entry.get('source', {}).get('repository') == MANAGER_REPOSITORY and
                 entry.get('source', {}).get('path') == MANAGER_SOURCE_PATH)]
    if len(matching) > 1 or (named and not matching):
        raise LibraryError('Resolve the conflicting or unrecognised Shared Skills Library manager record before setup.')
    return matching[0] if matching else None


def _manager_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?\d+(?:\.\d+)*', value):
        return None
    parts = [int(part) for part in value.lstrip('v').split('.')]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def scan_device(library, settings, roots=None, include_masters=False):
    selected = list(roots or settings.get('scan_roots') or [settings['skills_root']])
    if include_masters:
        selected.insert(0, str(library.root / 'library/skills'))
    legacy = read_json(library.root / 'data/shared-skills.json', {})
    descriptors = discover_standalone([Path(root) for root in selected], legacy=legacy)
    result = {'published': [], 'plugins': [], 'issues': [], 'observed': len(descriptors)}
    for descriptor in descriptors:
        metadata = dict(descriptor)
        source_path = Path(metadata.pop('path'))
        metadata.pop('observed_from', None)
        try:
            actual_hash = fingerprint(source_path)
            established_manager = manager_entry(library) if metadata['name'] == MANAGER_NAME else None
            if established_manager is not None and (metadata['source'].get('kind') in {'unknown', 'custom', 'local'} or
                                                    metadata['source'].get('repository') == MANAGER_REPOSITORY):
                # Keep older/edited device copies as unpromoted variants of the
                # existing manager even when this device has no legacy record.
                metadata = {key: deepcopy(value) for key, value in established_manager.items() if key not in {'package', 'versions', 'updated_at'}}
            same = [entry for entry in library.entries() if entry['kind'] == 'standalone' and entry['name'] == metadata['name'] and entry.get('package', {}).get('hash') == actual_hash]
            proposed_identity = entry_id('standalone', metadata['name'], metadata['source'])
            if len(same) == 1 and (metadata['source'].get('kind') == 'unknown' or proposed_identity == same[0]['id']):
                # Exact known bytes retain their established source identity.
                metadata = {key: deepcopy(value) for key, value in same[0].items() if key not in {'package', 'versions', 'updated_at'}}
            entry = library.publish(source_path, metadata)
            result['published'].append({'id': entry['id'], 'name': entry['name'], 'version': entry.get('version')})
        except (LibraryError, OSError) as error:
            result['issues'].append({'name': descriptor['name'], 'detail': str(error)})
    inventory, issue = plugin_inventory(available=True)
    plugin_entries = discover_plugins(codex_home(), inventory) if inventory is not None else []
    if issue:
        result['issues'].append({'name': 'plugin inventory', 'detail': issue})
    for descriptor in plugin_entries:
        identity = entry_id('plugin', descriptor['name'], descriptor['source'])
        descriptor['id'] = identity
        old = next((entry for entry in library.entries() if entry['id'] == identity), None)
        if old and old.get('version') != descriptor.get('version'):
            def numeric_version(value):
                return tuple(map(int, value.lstrip('v').split('.'))) if isinstance(value, str) and re.fullmatch(r'v?\d+(?:\.\d+)*', value) else None
            previous, observed = numeric_version(old.get('version')), numeric_version(descriptor.get('version'))
            if previous is None or observed is None or observed < previous:
                # Preserve a newer shared observation; receipts retain the real local version.
                descriptor = dict(old)
        try:
            library.save_entry(descriptor, expected=old)
            result['plugins'].append({'id': identity, 'parent': descriptor['plugin']['display_name'], 'skills': len(descriptor['skills'])})
        except LibraryError as error:
            result['issues'].append({'name': descriptor['name'], 'detail': str(error)})
    save_receipt(library, settings, plugin_entries if inventory is not None else None)
    return result


def configure(library, args, config_path):
    old = read_json(config_path, {})
    if old and Path(old['library_root']).resolve() != library.root:
        raise LibraryError('This device is already paired to another library; preserve its settings before changing the library root.')
    manager = Path(__file__).resolve().parents[2]
    # A normal URL installer can use either standard local skills root. Keep
    # that actual downloaded copy instead of creating another one elsewhere.
    downloaded_root = manager.parent if manager.parent.name == 'skills' and (
        manager.parent.parent.name in {'.agents', '.codex'} or manager.parent == codex_home() / 'skills') else None
    skills_root = Path(args.skills_root or old.get('skills_root') or downloaded_root or Path.home() / '.agents/skills').expanduser().absolute()
    settings = {'schema_version': 1, 'device_id': old.get('device_id') or str(uuid.uuid4()), 'name': args.device_name or old.get('name') or platform.node(),
                'platform': platform.system(), 'library_root': str(library.root), 'skills_root': str(skills_root),
                'scan_roots': old.get('scan_roots') or [str(skills_root), str(codex_home() / 'skills')], 'configured_at': old.get('configured_at') or now()}
    metadata = {'id': LEGACY_MANAGER_ID, 'name': MANAGER_NAME, 'kind': 'standalone', 'version': VERSION,
                'source': {'kind': 'github', 'repository': MANAGER_REPOSITORY, 'url': 'https://github.com/' + MANAGER_REPOSITORY,
                           'path': MANAGER_SOURCE_PATH, 'ref': 'main', 'tracking': 'branch', 'baseline_verified': False,
                           'evidence': 'Distribution location; an exact published source baseline has not yet been verified.'},
                'requirements': ['Python 3.9+ standard library', 'Google Drive desktop sync', 'Codex for native plugin inventory/installation'],
                'accounts_costs': 'No separate paid backend or API service. Uses your existing Drive and Codex access.', 'platforms': ['Windows', 'macOS'], 'author': 'WF Designz / Liki', 'license': 'No licence granted by publication alone'}
    entry = manager_entry(library)
    if entry is None:
        entry = library.publish(manager, metadata)
    else:
        # Device setup consumes the shared selection; it is not publication authority
        # for an older or edited executing copy on a different device.
        library.verify(entry['id'])
        if not old and downloaded_root is not None and fingerprint(manager) != entry['package']['hash']:
            raise LibraryError('The downloaded manager differs from the selected Drive package. Both are preserved; review the versions before completing setup.')
        target = skills_root / MANAGER_NAME
        target_hash = fingerprint(target) if target.exists() else None
        if target_hash is not None and target_hash != entry['package']['hash']:
            # A recorded content hash proves which published version is here.
            # Frontmatter readers may not recognise nested metadata.version.
            recorded_version = next((item.get('version') for item in entry.get('versions', []) if item.get('hash') == target_hash), None)
            local_version = _manager_version(recorded_version)
            shared_version = _manager_version(entry.get('version'))
            if local_version is None or shared_version is None or local_version >= shared_version:
                raise LibraryError('Setup will not downgrade or replace a differently selected manager version. Both copies are preserved; review them before requesting an update.')
    installed = library.install(entry['id'], skills_root)
    write_json(config_path, settings)
    save_receipt(library, settings)
    return settings, {'status': 'configured', 'device': settings['name'], 'config': str(config_path), 'installation': installed, 'other_devices': 'Pending their own setup and verification.'}


def installation_root(entry, settings, override=None):
    """Resolve a concrete observed copy without confusing same-name publishers."""
    configured = Path(settings['skills_root']).expanduser().absolute()
    roots = [Path(override).expanduser().absolute()] if override else [Path(value).expanduser().absolute() for value in [settings['skills_root'], *settings.get('scan_roots', [])]]
    unique = {str(root.resolve()): root for root in roots}
    known = {item.get('hash') for item in entry.get('versions', [])}
    if entry.get('package'):
        known.add(entry['package']['hash'])
    matching, different = [], []
    for root in unique.values():
        target = root / entry['name']
        if not target.exists():
            continue
        if target.is_symlink() or target.resolve().parent != root.resolve():
            raise LibraryError('Resolve the linked skill installation before changing it.')
        if fingerprint(target) in known:
            matching.append(root)
        else:
            different.append(root)
    if len(matching) > 1:
        raise LibraryError('Several matching local copies exist; select the intended --skills-root explicitly.')
    if matching:
        return matching[0]
    if different:
        raise LibraryError('The same-name local skill is modified or from a different publisher; reconcile its source before changing it.')
    return roots[0] if override else configured


def remove_local(library, settings, selector, skills_root=None):
    entry = library.get(selector)
    if entry['kind'] == 'plugin':
        return {'action': 'remove_plugin', 'plugin': entry['plugin'], 'detail': 'Use the normal whole-plugin uninstaller; no child skill files have been removed.'}
    root = installation_root(entry, settings, skills_root)
    target = root / entry['name']
    if not target.exists():
        return {'id': entry['id'], 'status': 'not_installed'}
    if target.is_symlink() or target.resolve().parent != root.resolve():
        raise LibraryError('Resolve the linked installation before removing it.')
    backup = root.parent / 'skill-backups/shared-skills-library' / uuid.uuid4().hex / entry['name']
    backup.parent.mkdir(parents=True)
    os.replace(target, backup)
    return {'id': entry['id'], 'status': 'removed_locally', 'backup_path': str(backup), 'shared_package': 'retained'}


def update_from_source(library, settings, entry, check, skills_root=None):
    if entry['kind'] == 'plugin':
        return {'action': 'update_plugin', 'plugin': entry['plugin'], 'detail': 'Update the whole parent plugin through its original provider, then rescan.'}
    revision = check.get('latest_revision')
    if not revision or check.get('status') not in {'update_available', 'baseline_unknown', 'source_changed'}:
        raise LibraryError('No newer or reviewable original-source revision was verified; inspect the source check before updating.')
    selected_root = installation_root(entry, settings, skills_root)
    target = selected_root / entry['name']
    if target.exists() and fingerprint(target) not in {item['hash'] for item in entry.get('versions', [])}:
        raise LibraryError('Local edits or a different source must be reconciled before an original-source update.')
    from .transfer import download_github_skill
    from .updates import resolve_revision
    import tempfile
    observed_release = revision if entry['source'].get('tracking') == 'release' else None
    revision = resolve_revision(entry['source'], revision)
    with tempfile.TemporaryDirectory(prefix='shared-skills-source-') as temporary:
        source = download_github_skill(entry['source'], revision, Path(temporary))
        metadata = {key: deepcopy(value) for key, value in entry.items() if key not in {'package', 'versions', 'updated_at'}}
        metadata['source'].update(revision=revision, baseline_verified=True, evidence='Complete package fetched from this immutable GitHub revision')
        if observed_release:
            metadata['source']['version'] = observed_release
        modes = read_json(source.parent / '.skill-library-transfer-modes.json', {'executables': []})
        metadata['portable_executables'] = modes['executables']
        metadata['requirements'] = list(metadata.get('requirements') or []) + ['Recheck current supporting files, software and account requirements after this source update.']
        metadata['accounts_costs'] = 'Not reverified after the upstream update; inspect the current source before use. Previous notes: ' + str(entry.get('accounts_costs') or 'Unknown')
        metadata['version'] = read_skill(source)['version']
        if metadata['version'] == 'unknown':
            metadata['version'] = revision[:12]
        updated = library.publish(source, metadata, promote=True)
        return library.install(updated['id'], selected_root)


def parser():
    command = argparse.ArgumentParser(description='Shared Skills Library v1.0.1')
    command.add_argument('--library-root', type=Path)
    command.add_argument('--config', type=Path)
    command.add_argument('--offline', action='store_true')
    command.add_argument('--json', action='store_true')
    subs = command.add_subparsers(dest='command', required=True)
    setup = subs.add_parser('setup')
    setup.add_argument('--device-name')
    setup.add_argument('--skills-root')
    subs.add_parser('list')
    search = subs.add_parser('search')
    search.add_argument('query', nargs='+')
    for name in ('show', 'install', 'update', 'remove'):
        child = subs.add_parser(name)
        child.add_argument('name')
        if name in {'install', 'update', 'remove'}:
            child.add_argument('--skills-root', type=Path)
        if name == 'update':
            child.add_argument('--from-source', action='store_true')
        if name == 'remove':
            child.add_argument('--from-library', action='store_true')
    for name in ('status', 'verify'):
        child = subs.add_parser(name)
        child.add_argument('name', nargs='?')
    scan = subs.add_parser('scan')
    scan.add_argument('--root', type=Path, action='append')
    scan.add_argument('--include-masters', action='store_true')
    add = subs.add_parser('add')
    add.add_argument('path', type=Path)
    add.add_argument('--source-url')
    add.add_argument('--source-path', default='.')
    add.add_argument('--source-ref', default='main')
    add.add_argument('--source-revision')
    add.add_argument('--version')
    add.add_argument('--promote', action='store_true')
    return command


def main(argv=None):
    # Native Windows pipes can default to a legacy code page, while publisher
    # names/descriptions are Unicode. The command interface consistently uses UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    args = parser().parse_args(argv)
    config_path = args.config or codex_home() / 'shared-skills-library/device.json'
    try:
        settings = read_json(config_path, {})
        root = args.library_root or settings.get('library_root') or os.environ.get('SKILL_LIBRARY_ROOT')
        if not root:
            raise LibraryError('Run setup from the shared Google Drive master before using this device.')
        library = Library(Path(root))
        if args.command == 'setup':
            settings, result = configure(library, args, config_path)
            selected = library.entries()
        else:
            if args.command not in {'list', 'search', 'show', 'status', 'verify'} and not settings.get('device_id'):
                raise LibraryError('Run setup on this device before changing installations or publishing packages.')
            entries = library.entries()
            selected = [library.get(args.name)] if getattr(args, 'name', None) else entries
            if args.command in {'list', 'status', 'search'}:
                result = deepcopy(selected)
                if args.command == 'search':
                    query = ' '.join(args.query).casefold()
                    result = [entry for entry in result if query in json.dumps(entry, ensure_ascii=False).casefold()]
                for entry in result:
                    entry['this_device'] = _local_status(entry, settings) if settings else {'status': 'device_not_configured'}
            elif args.command == 'show':
                result = deepcopy(selected[0])
                result['this_device'] = _local_status(result, settings)
            elif args.command == 'scan':
                result = scan_device(library, settings, args.root, args.include_masters)
                selected = library.entries()
            elif args.command == 'add':
                result = library.publish(args.path, metadata_for_source(args.path, args), promote=args.promote)
                selected = [result]
            elif args.command == 'install':
                destination = Path(settings['skills_root']) if selected[0]['kind'] == 'plugin' else installation_root(selected[0], settings, args.skills_root)
                result = library.install(args.name, destination)
            elif args.command == 'update':
                if args.from_source:
                    precheck = refresh_checks(library, selected, settings, args.offline)[0]
                    result = update_from_source(library, settings, selected[0], precheck, args.skills_root)
                    selected = [library.get(args.name)]
                elif selected[0]['kind'] == 'plugin':
                    result = {'action': 'update_plugin', 'plugin': selected[0]['plugin'], 'detail': 'Update the whole plugin through its original installation route.'}
                else:
                    result = library.install(args.name, installation_root(selected[0], settings, args.skills_root))
            elif args.command == 'remove':
                result = library.archive_entry(args.name) if args.from_library else remove_local(library, settings, args.name, args.skills_root)
                if args.from_library:
                    selected = []
            elif args.command == 'verify':
                result = [library.verify(entry['id']) for entry in selected]
            else:
                raise LibraryError('Unsupported library command')
        checks = refresh_checks(library, selected, settings, args.offline)
        save_receipt(library, settings)
        if args.command in {'list', 'status', 'search'}:
            for entry in result:
                entry['this_device'] = _local_status(entry, settings)
        elif args.command == 'show':
            result['this_device'] = _local_status(result, settings)
        render_catalogue(library)
        output = {'version': VERSION, 'result': result, 'checks': checks}
        if args.json:
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            print_human(output)
        return 0
    except (LibraryError, OSError, ValueError) as error:
        output = {'status': 'error', 'detail': str(error)}
        if args.json:
            print(json.dumps(output, ensure_ascii=False))
        else:
            print('Shared Skills Library: ' + str(error), file=sys.stderr)
        return 2


def print_human(output):
    result = output['result']
    if isinstance(result, list):
        for entry in result:
            label = 'From plugin: ' + entry.get('plugin', {}).get('display_name', entry.get('name', '')) if entry.get('kind') == 'plugin' else 'Standalone skill'
            print(str(entry.get('name') or entry.get('id')) + ' | ' + label + ' | ' + str(entry.get('version', entry.get('status', 'unknown'))))
    elif isinstance(result, dict):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    for check in output.get('checks', []):
        print('Check: ' + str(check['id']) + ' | ' + check['status'] + ' | ' + check.get('detail', ''))


if __name__ == '__main__':
    raise SystemExit(main())
