"""Shared Skills Library v1.0.1: read installed packages without executing them."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

EXCLUDED_NAMES = {"project-skill-advisor", "muapi-photo-pack-generator"}
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _clean_url(value):
    """Keep original public location, never userinfo or signed query strings."""
    if not isinstance(value, str) or not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        return None
    return urlunsplit((parts.scheme, parts.hostname, parts.path, "", ""))


def _frontmatter(folder):
    try:
        text = (folder / "SKILL.md").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None
    if not text.startswith("---"):
        return None
    blocks = text.split("---", 2)
    if len(blocks) < 3:
        return None
    values = {}
    lines = blocks[1].splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        if value in {">", ">-", "|", "|-"}:
            continuation = []
            for following in lines[index + 1:]:
                if following and not following[0].isspace():
                    break
                continuation.append(following.strip())
            value = " ".join(continuation).strip()
        values[key] = value.strip("\"'")
    name = values.get("name", "").strip()
    if not name or name.lower() in EXCLUDED_NAMES:
        return None
    return {"name": name, "description": values.get("description", ""),
            "version": values.get("version") or "unknown"}


def _source_from_evidence(name, legacy_entry, lock_entry):
    old_source = legacy_entry.get("source") or {}
    source = {"kind": "unknown", "baseline_verified": False}
    repository = old_source.get("repository") or old_source.get("upstream_repository")
    if isinstance(repository, str) and _REPO.fullmatch(repository):
        source.update(kind="github", repository=repository,
                      url="https://github.com/" + repository,
                      path=old_source.get("path", "."), ref=old_source.get("ref", "main"),
                      revision=legacy_entry.get("approved_revision") or old_source.get("upstream_revision"),
                      tracking="branch", evidence="legacy reference; content baseline not verified")
    elif old_source.get("kind") == "custom":
        source.update(kind="local", origin=old_source.get("origin", "user-authored"),
                      evidence="legacy custom-source record")
    elif old_source.get("provider"):
        source.update(kind="marketplace", provider=old_source["provider"],
                      evidence="legacy parent-plugin record; installation not verified")
    public_original = _clean_url(old_source.get("url") or old_source.get("origin"))
    if public_original:
        source["original_url"] = public_original
        source.setdefault("url", public_original)
    if lock_entry:
        locked_url = _clean_url(lock_entry.get("sourceUrl"))
        locked_repo = lock_entry.get("source")
        if not _REPO.fullmatch(str(locked_repo or "")) and locked_url:
            parts = urlsplit(locked_url)
            if parts.hostname == "github.com":
                locked_repo = parts.path.strip("/").removesuffix(".git")
        if lock_entry.get("sourceType") == "github" and _REPO.fullmatch(str(locked_repo or "")):
            path = lock_entry.get("skillPath", ".")
            if path.endswith("/SKILL.md"):
                path = path[:-len("/SKILL.md")]
            elif path == "SKILL.md":
                path = "."
            source = {"kind": "github", "repository": locked_repo,
                      "url": "https://github.com/" + locked_repo,
                      "original_url": locked_url or "https://github.com/" + locked_repo,
                      "path": path, "ref": lock_entry.get("ref", "main"),
                      "folder_hash": lock_entry.get("skillFolderHash"),
                      "baseline_verified": False, "tracking": "branch",
                      "evidence": "installer lock record; folder hash is not a commit baseline"}
    return source


def discover_standalone(roots, legacy=None, lock=None):
    """Read direct skill folders; historical metadata never proves current bytes."""
    old_by_name = {item.get("name"): item for item in (legacy or {}).get("skills", [])}
    lock_by_name = (lock or {}).get("skills", {})
    found = []
    seen_paths = set()
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        if lock is None:
            root_lock = _read_json(root.parent / ".skill-lock.json").get("skills", {})
        else:
            root_lock = lock_by_name
        for folder in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
            if (not folder.is_dir() or folder.is_symlink() or folder.name.startswith(".")
                    or folder.name.lower() in EXCLUDED_NAMES):
                continue
            real = folder.resolve()
            if real in seen_paths:
                continue
            info = _frontmatter(folder)
            if not info:
                continue
            seen_paths.add(real)
            old = old_by_name.get(info["name"], {})
            recommendation = old.get("recommendation") or {}
            requirements = list(recommendation.get("dependencies") or [])
            # These are evidence of file requirements, not a general code analyser.
            text = (folder / "SKILL.md").read_text(encoding="utf-8-sig")
            for relative in re.findall(r"\]\(([^)\s]+)\)", text):
                if relative.startswith("../"):
                    note = "External supporting file: " + relative
                    if note not in requirements:
                        requirements.append(note)
            info.update(kind="standalone", path=str(real),
                        source=_source_from_evidence(info["name"], old, root_lock.get(info["name"], {})),
                        author=old.get("author"), license=old.get("license", "unknown"),
                        requirements=requirements,
                        accounts_costs=recommendation.get("accounts_costs", "Unknown; inspect the skill's requirements."),
                        platforms=[recommendation.get("platform_support") or old.get("platform_support") or "Not verified"],
                        historical_version=old.get("version"),
                        observed_from="direct skill folder")
            found.append(info)
    return found


def _inside(root, relative):
    if not isinstance(relative, str):
        return None
    portable = PurePosixPath(relative.replace("\\", "/"))
    if portable.is_absolute() or ".." in portable.parts or ":" in relative:
        return None
    path = root.joinpath(*portable.parts)
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return path


def _plugin_manifest(root):
    portable = _read_json(root / "plugin.json")
    if portable:
        overlay = (portable.get("extensions") or {}).get("com.openai") or {}
        return dict(portable, **overlay)
    return _read_json(root / ".codex-plugin/plugin.json")


def _plugin_skills(root, manifest):
    declaration = manifest.get("skills", "./skills/")
    if isinstance(declaration, str):
        declaration = [declaration]
    result = {}
    for relative in declaration if isinstance(declaration, list) else []:
        location = _inside(root, relative)
        if location is None:
            continue
        if location.is_file() and location.name == "SKILL.md":
            locations = [location.parent]
        elif (location / "SKILL.md").is_file():
            locations = [location]
        elif location.is_dir():
            locations = sorted(path for path in location.iterdir() if path.is_dir() and not path.is_symlink())
        else:
            locations = []
        for skill_folder in locations:
            if skill_folder.name.startswith("."):
                continue
            info = _frontmatter(skill_folder)
            if info:
                result[info["name"]] = {"name": info["name"], "description": info["description"]}
    return sorted(result.values(), key=lambda item: item["name"].casefold())


def _plugin_entry(home, item, verified):
    name = item.get("name")
    marketplace = item.get("marketplaceName")
    if not name or not marketplace or name.lower() in EXCLUDED_NAMES:
        return None
    if any(segment in str(value) for value in (name, marketplace) for segment in ("/", "\\", "..")):
        return None
    version = str(item.get("version") or "unknown")
    root = home / "plugins/cache" / marketplace / name / version
    declared_source = item.get("source") or {}
    # Only a proven local plugin source may supersede its exact-version cache.
    if declared_source.get("source") == "local" and declared_source.get("path"):
        local_root = Path(declared_source["path"])
        if local_root.is_dir():
            root = local_root
    manifest = _plugin_manifest(root)
    interface = manifest.get("interface") or {}
    display_name = (interface.get("displayName") or interface.get("display_name")
                    or manifest.get("displayName") or manifest.get("name") or name)
    plugin_id = item.get("pluginId") or name + "@" + marketplace
    plugin = {"id": plugin_id, "name": name, "marketplace": marketplace,
              "display_name": display_name, "version": version,
              "install_reference": name + "@" + marketplace,
              "installed": bool(verified and item.get("installed") is True),
              "inventory_verified": verified, "enabled": item.get("enabled"),
              "source": {key: declared_source[key] for key in ("source", "id") if key in declared_source},
              "app_managed": marketplace in {"openai-primary-runtime", "openai-bundled"}}
    marketplace_source = item.get("marketplaceSource") or {}
    origin_url = _clean_url(marketplace_source.get("source"))
    source = {"kind": "marketplace", "marketplace": marketplace,
              "reference": plugin_id, "original_id": declared_source.get("id"),
              "url": origin_url, "baseline_verified": verified,
              "tracking": "provider", "evidence": "Codex installed inventory" if verified else "cache only; installation unverified"}
    latest = item.get("latestVersion") or item.get("latest_version")
    if latest:
        source["latest_version"] = str(latest)
    return {"name": name, "kind": "plugin", "description": manifest.get("description", ""),
            "version": version, "author": manifest.get("author"), "license": manifest.get("license", "unknown"),
            "source": source, "plugin": plugin, "skills": _plugin_skills(root, manifest),
            "requirements": ["Install the whole parent plugin through its original provider."],
            "accounts_costs": "Plugin account, connection and cost requirements remain device-local; check its original listing.",
            "platforms": ["Provider/client dependent"]}


def discover_plugins(codex_home, plugin_inventory=None):
    """Group installed plugin skills, without distributing cache or auth files."""
    home = Path(codex_home).expanduser()
    found = []
    if plugin_inventory is not None:
        for item in plugin_inventory.get("installed", []):
            if not isinstance(item, dict) or item.get("installed") is not True:
                continue
            entry = _plugin_entry(home, item, True)
            if entry:
                found.append(entry)
    else:
        cache = home / "plugins/cache"
        if cache.is_dir():
            for market in sorted(cache.iterdir()):
                if not market.is_dir() or market.is_symlink() or market.name.startswith("."):
                    continue
                for plugin in sorted(market.iterdir()):
                    if not plugin.is_dir() or plugin.is_symlink() or plugin.name.startswith("."):
                        continue
                    versions = sorted(path for path in plugin.iterdir() if path.is_dir() and not path.is_symlink())
                    if not versions:
                        continue
                    sampled = versions[-1]
                    entry = _plugin_entry(home, {"name": plugin.name, "marketplaceName": market.name,
                                                "version": sampled.name, "source": {}}, False)
                    if entry:
                        entry["version"] = "unknown"
                        entry["plugin"]["version"] = "unknown"
                        entry["plugin"]["cache_versions"] = [path.name for path in versions]
                        found.append(entry)
    return sorted(found, key=lambda item: (item["plugin"]["display_name"].casefold(), item["plugin"]["id"]))
