"""Fresh, read-only checks of the executing manager, Shared Skills Library v1.0.3."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.error import HTTPError, URLError

from .store import fingerprint
from .transfer import download_github_skill
from .updates import _fetch_json

REPOSITORY = 'liki-udam/shared-skills-library'
SOURCE_PATH = 'skills/shared-skills-library'
SOURCE = {'kind': 'github', 'repository': REPOSITORY, 'path': SOURCE_PATH, 'ref': 'main'}
COMMIT = re.compile(r'^[0-9a-fA-F]{40}$')


def latest_revision():
    """Prefer the API, then Git's public ref discovery; never prompt for access."""
    try:
        result = _fetch_json('https://api.github.com/repos/' + REPOSITORY + '/commits/main')
        revision = result.get('sha') if isinstance(result, dict) else None
        if isinstance(revision, str) and COMMIT.fullmatch(revision):
            return revision
    except (OSError, ValueError, TypeError):
        pass
    import os
    result = subprocess.run(
        ['git', '-c', 'credential.interactive=false', 'ls-remote',
         'https://github.com/' + REPOSITORY + '.git', 'refs/heads/main'],
        capture_output=True, text=True, timeout=12,
        env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    revision = result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else ''
    if not COMMIT.fullmatch(revision):
        raise ValueError('The publisher revision could not be verified')
    return revision


def check_manager(manager, entry=None, *, offline=False):
    """Compare real running bytes to fresh publisher bytes, without library writes.

    A saved source revision is useful only when its pinned archive matches the
    executing package. In particular, another device's shared version is not
    treated as evidence that this device is current.
    """
    result = {'status': 'verification_failed',
              'checked_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
              'source_url': 'https://github.com/' + REPOSITORY,
              'latest_revision': None,
              'detail': 'Manager freshness could not be verified. Library work is paused.'}
    if offline:
        result['detail'] = 'Offline mode cannot verify manager freshness. Library work is paused.'
        return result
    try:
        actual = fingerprint(Path(manager))
        revision = latest_revision()
        result['latest_revision'] = revision
        with tempfile.TemporaryDirectory(prefix='shared-skills-manager-check-') as temporary:
            latest = download_github_skill(SOURCE, revision, Path(temporary) / 'latest')
            remote_hash = fingerprint(latest)
            result.update(running_hash=actual, latest_hash=remote_hash)
            if actual == remote_hash:
                result.update(status='current', detail='The running manager matches the current publisher package.')
                return result
            # Prove the running baseline independently of the selected shared hash.
            verified = False
            history = (entry or {}).get('versions', [])
            if not isinstance(history, list) or any(not isinstance(item, dict) or
                    not isinstance(item.get('source', {}), dict) for item in history):
                result['detail'] = 'Manager source history is damaged. Library work is paused.'
                return result
            sources = [item.get('source') or {} for item in history
                       if item.get('hash') == actual]
            sources.append((entry or {}).get('source') or {})
            seen = {revision}
            for source in sources:
                baseline = source.get('revision')
                if (source.get('kind') != 'github' or source.get('repository') != REPOSITORY
                        or source.get('path') != SOURCE_PATH or not isinstance(baseline, str)
                        or not COMMIT.fullmatch(baseline) or baseline in seen):
                    continue
                seen.add(baseline)
                previous = download_github_skill(SOURCE, baseline, Path(temporary) / ('baseline-' + str(len(seen))))
                if fingerprint(previous) == actual:
                    verified = True
                    result['current_revision'] = baseline
                    break
            if verified:
                result.update(status='update_available',
                              detail='A Shared Skills Library update is pending. Library work is paused; request the manager update first.')
            else:
                result['detail'] = 'The running manager differs from the publisher and its baseline could not be verified. Local files are preserved; library work is paused.'
    except HTTPError as error:
        result['detail'] = 'Publisher package download failed with HTTP ' + str(error.code) + '. Library work is paused.'
    except (subprocess.TimeoutExpired, TimeoutError):
        result['detail'] = 'The manager source check timed out. Library work is paused.'
    except URLError:
        result['detail'] = 'The manager publisher could not be reached. Library work is paused.'
    except FileNotFoundError:
        result['detail'] = 'A required package file or Git fallback executable is unavailable. Library work is paused.'
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        result['detail'] = 'The publisher source or running package could not be validated. Library work is paused.'
    return result
