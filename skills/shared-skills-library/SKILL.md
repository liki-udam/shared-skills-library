---
name: shared-skills-library
description: Manage Liki's Google Drive skills library across Windows and Mac. Find skills, check their original sources for updates, scan existing installations, and add, install, update or remove complete standalone packages. Use during authorised skill installations to record them in the shared library. Plugin-supplied skills are labelled and installed through their whole parent plugin.
metadata:
  version: "1.0.1"
---

# Shared Skills Library

Version 1.0.1. This manages packages and installation records. Project Skill Advisor remains retired; do not invoke it or restore its registration/approval machinery.

## Start here

Use the bundled `scripts/library.py` with Python 3.9 or newer. It uses only the standard library. Resolve paths from this installed skill, never from the current project. Read [usage and setup](references/usage.md) for the command interface and first-device setup.

Local settings live under effective `CODEX_HOME/shared-skills-library/device.json` (default `~/.codex`). They locate the synced Drive root and this device's real local skills folder. Do not assume another device uses the same absolute Drive path.

## Installation from GitHub and first use

The public package is [liki-udam/shared-skills-library](https://github.com/liki-udam/shared-skills-library/tree/main/skills/shared-skills-library). Install this complete folder, including scripts and references. An authorised request to install this manager includes pairing it with the existing shared library on this device. Check both standard local skill roots before installation so an existing manager is not duplicated.

Read the saved device settings first. If unconfigured, inspect the device's normal Google Drive locations for the existing library folder containing `data/skill-library/entries`. On Windows, inspect available Drive mounts; on Mac, inspect Google Drive under `~/Library/CloudStorage`. Do not create a separate library because a synced folder has not yet been located. Ask for the location only if targeted discovery finds none or several plausible libraries.

From the installed skill, run `scripts/bootstrap.py --library-root PATH` with the actual synced library root. The same bootstrap without that option still works from the Drive master or with saved device settings. It preserves the actual downloaded installation root on a fresh device. If downloaded and selected shared contents differ, report the difference and preserve both rather than forcing replacement. After pairing, scan this device and verify its packages. Do not install the other library skills automatically. Explain any missing Python or Drive access precisely; installation of the skill alone does not establish a working library connection.

Run a library command whenever using this skill. Commands check this device against shared records and check relevant upstream sources. Keep the resulting check statuses and dates visible in the answer. A failed/offline/unknown-source check is incomplete, not evidence of being up to date. Device receipts describe the last observation on that device, not a live remote inventory. No background watcher is needed.

## Find and install

Use `list`, `search` or `show`. Describe purpose, original source, version, requirements and any account/cost dependencies. Unknown fields stay unknown. Do not select between ambiguous same-name entries without the user's choice of source.

For **Standalone skill**, `install` copies the complete verified package into this device's configured skills folder. It does not execute its scripts or install its dependencies. Check actual supporting files and requirements before claiming the skill is usable. Preserve local edits and previous copies; do not use replacement options merely to get past a conflict.

For **From plugin: <parent>**, an install/update request means the **whole parent plugin**, not the individual child skill. The command returns the parent plugin's original ID, marketplace/source and native installation route. Use the available native plugin installer or its normal installation UI/CLI. App-managed plugins use the app's own update route. Keep account sign-in on the device. Rescan afterwards and verify the parent and its child skills; an installation plan or UI suggestion alone does not prove installation.

## Add, scan and update

When the user authorises installing a new standalone skill, carry out that installation using its actual source and supported installer, then `add` the complete resulting package with original source metadata. No separate adviser/publication approval is required by this library. Do not run unreviewed downloaded code simply to catalogue it. When this manager installs from an existing shared entry, publication is already satisfied by its immutable package.

`scan` discovers standard local skill roots and verified installed plugins. Existing shared masters and the old registry can supply source evidence, but historical versions are not verified current source baselines. Import complete packages only. If a package references files outside its folder, record that dependency and establish how the destination gets it. Missing files, paid services and platform-specific requirements must be stated; never silently purchase services or create accounts.

Checks distinguish device updates from original-publisher updates. Apply an update only when requested. `update` installs a newer shared package by default; `update --from-source` stages a pinned newer GitHub revision before publication/installation. Inspect material instruction, dependency and cost changes before using it. Preserve local modifications; a changed source or missing baseline may require comparison rather than a blind update.

## Removal and boundaries

`remove` uninstalls on this device by moving the installation to its local backups; the Drive package remains. `remove --from-library` archives the shared entry and excludes it from reimport, without uninstalling other devices. Archives, discarded trials, Project Skill Advisor and the removed MuAPI photo-pack skill are excluded from scans.

Never synchronise Codex's entire settings folder, plugin caches, credentials or runtime environments. Never claim a lock across computers. If Drive presents conflicted records or an incomplete package, preserve the evidence and resolve it before replacement. Preserve normal existing user authority; do not introduce additional approval loops for an unchanged authorised action.
