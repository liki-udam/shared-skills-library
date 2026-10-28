#!/bin/sh
# Shared Skills Library setup v1.0.1. Uses existing Python only.
set -eu
task_script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
for task_python in python3 python /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$task_python" >/dev/null 2>&1 && "$task_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 2)' >/dev/null 2>&1; then
        exec "$task_python" "$task_script_dir/bootstrap.py" "$@"
    fi
done
printf '%s\n' 'Python 3.9+ was not found. Ask Codex to locate its bundled Python and run bootstrap.py. Nothing was installed.' >&2
exit 2
