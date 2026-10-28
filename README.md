# Shared Skills Library

Version 1.0.1. A standalone Codex skill for managing a Google Drive skills library across Windows and macOS.

## Install in Codex

Paste this into a Codex chat on the destination device:

> Install this skill: https://github.com/liki-udam/shared-skills-library/tree/main/skills/shared-skills-library

Codex should install the complete skill, locate your existing synced Google Drive library, pair this device, then scan and verify it. It should ask for the library folder only if it cannot locate it or finds several candidates. The skill is available on the next turn after installation. Keep this installation request in the same chat so Codex can finish setup immediately.

For agents using the standard Codex skill installer, the repository is `liki-udam/shared-skills-library` and the skill path is `skills/shared-skills-library`. Check both normal local skill roots before installing so an existing manager is not duplicated. Read the installed `SKILL.md` and its setup reference before pairing the device.

## What it does

- Finds standalone skills and whole plugins in your shared library.
- Shows original sources, versions, requirements, costs and dated device observations.
- Checks relevant original sources whenever you use the library, and applies updates only when requested.
- Preserves local edits, keeps complete portable packages and backs up known previous installations.
- Labels plugin-supplied skills with their parent plugin and routes installation through the whole plugin.

GitHub distributes this management skill. Your existing Google Drive folder remains the shared catalogue and package store. This repository contains no personal catalogue, other skill packages, device records, credentials or plugin caches. Project Skill Advisor remains retired.

## Requirements and setup

Use Codex with local filesystem access, Python 3.9 or newer, and Google Drive desktop sync. There is no added paid backend or API service. Plugin inventory and installation depend on Codex's native plugin tools and each plugin's own requirements.

Let Drive finish syncing the existing library. From the installed skill's `scripts` directory, pair with:

```text
python bootstrap.py --library-root "PATH TO YOUR EXISTING SYNCED LIBRARY"
```

On Mac, the interpreter may be `python3`. The root is the folder containing `data/skill-library/entries`, not the installed manager. An existing setup keeps its device identity and local paths. A fresh URL installation keeps its actual local skills root. Differing downloaded/shared packages are preserved for review; setup does not silently replace the shared selection.

You can still run `python bootstrap.py` directly from the shared Drive master, or from an installed copy with saved settings. Setup does not install Python or automatically install every skill in your library. See [complete usage and setup](skills/shared-skills-library/references/usage.md).

This release was tested on Windows and in isolated installation/setup tests. Actual setup on another Windows device or a Mac must be verified on that device.

## Tests

```text
python -m unittest discover -s tests -p "test_skill_library*.py" -v
```

Tests use temporary libraries and mocked source responses. They do not need your Drive catalogue or GitHub sign-in.
