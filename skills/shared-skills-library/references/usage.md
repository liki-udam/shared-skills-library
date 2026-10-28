# Shared Skills Library v1.0.1

## Using it in chat

- “Show my shared skills and available updates.”
- “Find the animation skill in my library.”
- “Install Website Workflow from my library on this device.”
- “Add the skill I just installed to my shared library, retaining its original source.”
- “Scan this device for skills and plugins missing from the library.”
- “Update Humanizer from its original source and share the new version.”
- “Remove this skill from this device.”

Every substantive use checks for updates. A check reports available changes; it does not apply them. When a publisher does not expose a verifiable version, the result explains what cannot be established. No scheduled or background task is installed.

## Set up another Windows computer or Mac

You can install the complete manager directly from GitHub by pasting this request into Codex:

> Install this skill: https://github.com/liki-udam/shared-skills-library/tree/main/skills/shared-skills-library

Codex should check for an existing manager, install its complete folder, locate your existing synced Google Drive library and pair this device. GitHub stores the management skill; Drive still stores your catalogue and shared skill packages. No GitHub sign-in is needed to download this public package. Your existing Drive and Codex access are still required.

If Drive is not yet mounted or several libraries are plausible, Codex will need the correct local folder. From the downloaded skill's `scripts` directory, the complete pairing command is:

```text
python bootstrap.py --library-root "PATH TO YOUR EXISTING SYNCED LIBRARY"
```

The path is the folder containing `data/skill-library/entries`, not the installed manager folder. Keep it in quotes when it contains spaces. A fresh setup uses the downloaded copy's existing local skills root; an established setup keeps its saved root. Different downloaded/shared copies are preserved for review. Setup does not silently promote a downloaded package over the shared selection.

Let Google Drive finish syncing this shared folder, including `library/skills/shared-skills-library`. Make it available offline if your Drive client normally streams files. Open a Codex chat on that device with access to the folder and ask:

> Read the Shared Skills Library skill at `library/skills/shared-skills-library/SKILL.md` in this Google Drive folder. Run its bootstrap setup on this device, scan my installed standalone skills and whole plugins, and verify the result. Do not install all the other library skills automatically.

The agent must resolve that device's actual Drive root. Python 3.9+ is required, using the existing system or Codex-bundled interpreter where available. Setup does not install Python or another service. If neither interpreter is available, report that specific dependency before continuing.

From the shared master's `scripts` directory, the complete setup command is:

```text
python bootstrap.py
```

On Mac the interpreter may be named `python3`. `--device-name` gives a friendly label, and `--skills-root` can select an existing local skill root. Otherwise setup uses `~/.agents/skills`. Shared packages, settings and receipts have different roles: Drive stores portable packages; local settings store this computer's paths; a dated receipt tells other devices what was last observed here.

The complete convenience launchers are `scripts/setup-windows.ps1` and `scripts/setup-mac.command`. They locate an existing interpreter and call the same bootstrap. For a downloaded installation, the Windows launcher accepts `-LibraryRoot "PATH"`; the Mac launcher accepts `--library-root "PATH"`. They do not install runtimes or change account settings. If double-clicking a launcher is blocked by the operating system, use the Codex setup request above instead of changing security settings.

## Command interface

Run the installed `scripts/library.py` through the available Python interpreter. Global options go before the command:

```text
python library.py [--library-root PATH] [--config PATH] [--offline] [--json] COMMAND
```

`--offline` explicitly skips online checks and reports them incomplete. It is useful for disconnected setup/tests; do not use it to imply an online check passed.

| Command | Behaviour |
|---|---|
| `setup [--device-name NAME] [--skills-root PATH]` | Register this device and install the selected shared manager. Initialise its package only if no shared record exists. |
| `list`, `search WORDS`, `status [NAME]` | Show library entries and dated update results. |
| `show NAME` | Show source, package, plugin parent, requirements and update detail. |
| `scan [--root PATH] [--include-masters]` | Publish missing complete standalone packages and catalogue verified installed whole plugins. Conflicting local versions remain visible. |
| `add PATH [--source-url URL] [--source-path PATH] [--source-ref REF] [--source-revision SHA] [--version VERSION]` | Publish a complete local skill with its original source. `--promote` explicitly chooses changed content as the shared version. |
| `install NAME` | Verify/install a standalone skill, or return the whole parent-plugin installation route. |
| `update NAME [--from-source]` | Install the shared version, or fetch a newer pinned GitHub source package before publication and installation. |
| `verify [NAME]` | Check saved package contents against their manifests. |
| `remove NAME [--from-library]` | Reversibly remove this device's installation, or separately archive/exclude the shared entry. |

Names can be entry IDs. A plugin child's name resolves to its parent; ambiguous names require a full entry ID. Publication never executes the package's code. An installation's file integrity is separate from whether its external software/account dependencies are ready.

Install, update and local removal target an existing detected copy of the selected source, including copies in `~/.codex/skills`. If several matching copies exist, select `--skills-root PATH` explicitly. A same-name installation from another publisher is not overwritten or removed.

## How records work

`data/skill-library/entries` stores one live record per package/plugin. `library/packages` stores immutable, complete standalone versions. `data/skill-library/devices` contains separate dated receipts for each configured device, and `data/skill-library/checks` contains its latest source checks. `Shared Skills Library.md` is a readable generated view, not the source of truth. The previous `data/shared-skills.json` remains historical evidence.

Original-source information can come from a verified installer receipt, npx lock record, plugin inventory or historical source notes. Historical claims are labelled evidence until compared with the actual package. Private/local sources may need manual comparison. Marketplace plugins are updated through their original provider; if its current version is not exposed by the native catalogue, the result says it could not verify a newer version.

Package hashes protect installation from incomplete Drive downloads and changed files. Local backups remain outside skill discovery. These checks are not a distributed lock: simultaneous publishers can still create Drive conflicts, which must be resolved visibly. Runtime caches, secrets, dependency environments and plugin connection state are not portable package content.
