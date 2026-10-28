#!/usr/bin/env python3
"""Shared Skills Library v1.0.1: pair a downloaded skill or shared master."""
import argparse
from pathlib import Path
import sys

from skill_library.cli import codex_home, main as library_main, read_json
from skill_library.store import LibraryError


def main(argv=None):
    if sys.version_info < (3, 9):
        print('Shared Skills Library needs Python 3.9 or newer.', file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library-root', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--device-name')
    parser.add_argument('--skills-root', type=Path)
    args = parser.parse_args(argv)
    config = args.config or codex_home() / 'shared-skills-library/device.json'
    try:
        settings = read_json(config, {})
        manager = Path(__file__).resolve().parents[1]
        inferred = manager.parents[2] if len(manager.parents) >= 3 else None
        if inferred is not None and manager != inferred / 'library/skills/shared-skills-library':
            inferred = None
        root = args.library_root or settings.get('library_root') or inferred
        if not root:
            raise LibraryError('Locate the existing synced Google Drive library and rerun with --library-root PATH. No library was created.')
        root = Path(root).expanduser().resolve()
        if not root.is_dir() or not ((root / 'data/skill-library/entries').is_dir() or
                                     (root / 'library/skills/shared-skills-library/SKILL.md').is_file()):
            raise LibraryError('The --library-root location is missing or is not an existing Shared Skills Library. Wait for Drive sync or locate the correct folder.')
    except (LibraryError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    command = ['--library-root', str(root), '--config', str(config)]
    for flag in ('offline', 'json'):
        if getattr(args, flag):
            command.append('--' + flag)
    command.append('setup')
    if args.device_name:
        command.extend(['--device-name', args.device_name])
    if args.skills_root:
        command.extend(['--skills-root', str(args.skills_root)])
    return library_main(command)


if __name__ == '__main__':
    raise SystemExit(main())
