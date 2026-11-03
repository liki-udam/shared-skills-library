"""Run legacy command scenarios with only the manager preflight mocked current.

The preflight has its own real guard tests. All other CLI/bootstrap behaviour,
including their filesystem operations and offline package checks, runs normally.
"""
import subprocess


def run_command(command, **kwargs):
    wrapper = '''
import sys, runpy
from pathlib import Path
from unittest.mock import patch
target = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path.insert(0, str(Path(target).parent))
with patch('skill_library.cli.check_manager', return_value={'status': 'current', 'detail': 'Fixture manager'}):
    runpy.run_path(target, run_name='__main__')
'''
    return subprocess.run([command[0], '-c', wrapper, *command[1:]], **kwargs)
