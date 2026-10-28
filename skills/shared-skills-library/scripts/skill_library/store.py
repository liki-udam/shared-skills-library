"""Portable, verified skill packages and reversible local installation, v1.0.1.

Each package is immutable. Expected-content checks detect stale local writers;
they are not a distributed lock against another computer's Drive client.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import unicodedata
import uuid
from urllib.parse import urlsplit


class LibraryError(ValueError):
    """A library operation could not be completed without losing information."""


EXCLUDED_NAMES = {"project-skill-advisor", "muapi-photo-pack-generator"}
IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", "runtime"}
ARCHIVE_DIRS = {"archive", "archives", "backups", "skill-backups", "removed-skills"}
SECRET_NAMES = {"credentials.json", "secrets.json", "tokens.json", "id_rsa", "id_ed25519"}
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
IDENTITY = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {prefix + number for prefix in ("COM", "LPT") for number in "123456789¹²³"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _portable_relative(relative):
    if not isinstance(relative, str):
        raise LibraryError("Library paths must be strings")
    text = relative
    rel = PurePosixPath(text)
    if not text or "\\" in text or rel.is_absolute() or PureWindowsPath(text).drive or any(p in {"", ".", ".."} for p in text.split("/")):
        raise LibraryError("Unsafe relative library path: " + text)
    for part in rel.parts:
        reserved = part.split(".", 1)[0].rstrip(" .").upper() in WINDOWS_RESERVED
        if reserved or part.endswith((" ", ".")) or any(ord(char) < 32 or char in '<>:"\\|?*' for char in part):
            raise LibraryError("File name is not portable between Windows and macOS: " + text)
    return rel


def _register_path(seen, relative, *, is_file=True):
    parts = _portable_relative(relative).parts
    for index in range(1, len(parts) + 1):
        original = "/".join(parts[:index])
        canonical = unicodedata.normalize("NFC", original).casefold()
        kind = "file" if index == len(parts) and is_file else "directory"
        previous = seen.get(canonical)
        if previous and (previous != (original, kind) or kind == "file"):
            raise LibraryError("File or directory names collide on Windows/macOS: " + original)
        seen[canonical] = (original, kind)


def _safe_child(base, relative):
    rel = _portable_relative(relative)
    base = Path(base).resolve()
    target = base.joinpath(*rel.parts)
    try:
        target.resolve().relative_to(base)
    except ValueError:
        raise LibraryError("Library path escapes its intended directory: " + relative)
    return target


def _is_secret(name):
    lowered = name.lower()
    env_secret = lowered == ".env" or (lowered.startswith(".env.") and lowered not in {".env.example", ".env.sample", ".env.template"})
    return env_secret or lowered in SECRET_NAMES or lowered.endswith((".pem", ".key", ".p12", ".pfx"))


def _files(path, *, ignore_environments=True):
    path = Path(path).absolute()
    if not path.is_dir() or _linked(path):
        raise LibraryError("A real skill directory is required: " + str(path))
    result, seen = [], {}
    for directory, dirs, files in os.walk(path, followlinks=False):
        directory = Path(directory)
        for child in list(dirs):
            item = directory / child
            if _linked(item):
                raise LibraryError("Linked directories cannot be included in a skill package: " + str(item))
            if ignore_environments and child.lower() in IGNORED_DIRS:
                dirs.remove(child)
            else:
                _register_path(seen, item.relative_to(path).as_posix(), is_file=False)
        for name in sorted(files):
            if name == ".DS_Store":
                continue
            item = directory / name
            if _linked(item) or not item.is_file():
                raise LibraryError("Linked or special files cannot be included in a skill package: " + str(item))
            if _is_secret(name):
                raise LibraryError("Keep credentials outside skill packages: " + str(item.relative_to(path)))
            _register_path(seen, item.relative_to(path).as_posix())
            data = item.read_bytes()
            result.append({
                "path": item.relative_to(path).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "executable": bool(item.stat().st_mode & 0o111) or item.suffix.lower() in {".sh", ".command"},
            })
    return sorted(result, key=lambda item: item["path"])


def _digest(files):
    # Executable bits are retained in the manifest but not the portable content
    # hash: Windows copies must remain identical to the same package on macOS.
    contents = [{"path": item["path"], "sha256": item["sha256"]} for item in files]
    return hashlib.sha256(_json(contents).encode("utf-8")).hexdigest()


def fingerprint(path: Path) -> str:
    """Hash portable file names and bytes, excluding machine-local environments."""
    return _digest(_files(path))


def read_skill(path: Path) -> dict:
    """Read essential plain frontmatter without executing or depending on YAML."""
    path = Path(path)
    target = path / "SKILL.md" if path.is_dir() else path
    if not target.is_file():
        raise LibraryError("SKILL.md not found: " + str(target))
    if _linked(target):
        raise LibraryError("SKILL.md must be a real file")
    text = target.read_text(encoding="utf-8-sig")
    match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not match:
        raise LibraryError("SKILL.md needs name and description frontmatter")
    lines = match.group(1).splitlines()
    result = {}
    for index, line in enumerate(lines):
        found = re.match(r"^(name|description|version):\s*(.*)$", line)
        if not found:
            continue
        key, value = found.groups()
        if value in {">", ">-", "|", "|-"}:
            folded = []
            for following in lines[index + 1:]:
                if following and not following[0].isspace():
                    break
                folded.append(following.strip())
            value = " ".join(folded).strip()
        result[key] = value.strip().strip("\"'")
    if not NAME.fullmatch(result.get("name", "")):
        raise LibraryError("Invalid skill name in SKILL.md")
    if not result.get("description"):
        raise LibraryError("SKILL.md description is required")
    result.setdefault("version", "unknown")
    return result


def entry_id(kind, name, source: dict) -> str:
    """Identify names within their original publisher/source rather than by name alone."""
    source = source or {}
    repository = source.get("repository") or source.get("repo") or source.get("upstream_repository")
    url = source.get("url") or source.get("source_url") or source.get("sourceUrl")
    origin = repository or url or source.get("origin") or source.get("id") or "unknown"
    if kind == "plugin":
        # Parent-plugin references are scoped to their marketplace. A remote
        # plugin may have no URL at all, or share a repository with other plugins.
        origin = source.get("reference") or source.get("install_reference") or source.get("parent_reference")
        if not origin:
            original = source.get("original_id") or source.get("id") or repository or url or source.get("origin") or "unknown"
            origin = str(source.get("marketplace") or "unknown") + "|" + str(original)
    elif url and not repository:
        parts = urlsplit(str(url))
        if parts.hostname == "github.com" and len(parts.path.strip("/").split("/")) >= 2:
            origin = "/".join(parts.path.strip("/").split("/")[:2]).removesuffix(".git")
        else:
            origin = str(url).rstrip("/")
    identity_origin = str(origin) if kind == "plugin" else str(origin).lower()
    identity = [kind, str(name), identity_origin, str(source.get("path") or source.get("skill_path") or "").rstrip("/")]
    suffix = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-")[:64] or "unnamed"
    return str(kind) + "-" + slug + "-" + suffix


class Library:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.state = self.root / "data" / "skill-library"
        self.entry_dir = self.state / "entries"
        self.exclusions = self.state / "exclusions"

    def _validate(self, entry):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not IDENTITY.fullmatch(entry["id"]):
            raise LibraryError("Invalid library entry identity")
        if entry.get("kind") not in {"standalone", "plugin"}:
            raise LibraryError("Entry kind must be standalone or plugin")
        name = entry.get("name", "")
        if not isinstance(name, str) or (entry["kind"] == "standalone" and not NAME.fullmatch(name)):
            raise LibraryError("Invalid library skill name")
        if name in EXCLUDED_NAMES:
            raise LibraryError("This skill has been retired or removed: " + name)
        if not isinstance(entry.get("source", {}), dict):
            raise LibraryError("Entry source must be an object")
        package = entry.get("package")
        if package is not None:
            if not isinstance(package, dict) or not package:
                raise LibraryError("Package manifest must be a nonempty object")
            # Reading catalogue metadata must not walk/hydrate every package
            # directory on Drive. Resolve real paths at verification/copy time.
            _portable_relative(package.get("path", ""))
            files = package.get("files")
            if not isinstance(files, list) or not files:
                raise LibraryError("Package file manifest is missing")
            seen = {}
            for item in files:
                if not isinstance(item, dict):
                    raise LibraryError("Each package file must be a manifest object")
                relative = item.get("path", "")
                _register_path(seen, relative)
                checksum = item.get("sha256")
                if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
                    raise LibraryError("Invalid or duplicate package manifest file")
                if "executable" in item and not isinstance(item["executable"], bool):
                    raise LibraryError("Executable manifest flags must be booleans")
            checksum = package.get("hash")
            if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
                raise LibraryError("Invalid package checksum")
            if _digest(files) != checksum:
                raise LibraryError("Package checksum does not match its file manifest")
        return entry

    def _is_excluded(self, identity):
        return (self.exclusions / (identity + ".json")).exists()

    def entries(self) -> list[dict]:
        if not self.entry_dir.exists():
            return []
        entries, seen = [], set()
        for path in sorted(self.entry_dir.glob("*.json")):
            if _linked(path):
                raise LibraryError("Linked catalogue records are not supported")
            try:
                entry = self._validate(json.loads(path.read_text(encoding="utf-8-sig")))
            except (OSError, ValueError, TypeError, AttributeError) as error:
                raise LibraryError("Cannot read catalogue record " + path.name + ": " + str(error)) from error
            identity = entry["id"]
            if identity in seen or path.name != identity + ".json":
                raise LibraryError("Conflicting Drive catalogue copies need reconciliation: " + identity)
            seen.add(identity)
            if not self._is_excluded(identity):
                entries.append(entry)
        return entries

    def get(self, selector: str) -> dict:
        entries = self.entries()
        exact = [item for item in entries if item["id"] == selector]
        if exact:
            return exact[0]
        matches = []
        for entry in entries:
            children = [child.get("name") if isinstance(child, dict) else child for child in entry.get("skills", [])]
            if entry.get("name") == selector or selector in children or entry.get("plugin", {}).get("id") == selector:
                matches.append(entry)
        if len(matches) != 1:
            raise LibraryError("Ambiguous skill name; select its full catalogue ID" if matches else "Skill not found in the shared library: " + selector)
        return matches[0]

    @contextmanager
    def _write_lock(self, identity):
        self.entry_dir.mkdir(parents=True, exist_ok=True)
        path = self.entry_dir / (identity + ".lock")
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise LibraryError("A local catalogue write is pending; inspect before retrying: " + str(path))
        try:
            os.close(handle)
            yield
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(str(temporary), str(path))
        finally:
            temporary.unlink(missing_ok=True)

    def save_entry(self, entry: dict, *, expected=None) -> dict:
        entry = deepcopy(entry)
        for key in ("local_path", "source_dir", "installed_path"):
            entry.pop(key, None)
        self._validate(entry)
        identity = entry["id"]
        if self._is_excluded(identity):
            raise LibraryError("This entry was removed from the shared library")
        with self._write_lock(identity):
            # Read again immediately before replacing, including Drive conflict copies.
            current = next((item for item in self.entries() if item["id"] == identity), None)
            if expected is not None and current != expected:
                raise LibraryError("Catalogue changed since it was read; reconcile before saving")
            if expected is None and current is not None and current != entry:
                raise LibraryError("Catalogue entry already exists; supply its current record to update it")
            if current == entry:
                return current
            entry["updated_at"] = _now()
            self._write_json(self.entry_dir / (identity + ".json"), entry)
            return entry

    def _verify_package(self, package):
        path = _safe_child(self.root, package["path"])
        if not path.is_dir():
            raise LibraryError("Shared package is not fully available; wait for Drive sync")
        actual = _files(path, ignore_environments=False)
        expected = [{"path": item["path"], "sha256": item["sha256"]} for item in package["files"]]
        contents = [{"path": item["path"], "sha256": item["sha256"]} for item in actual]
        if contents != expected or _digest(actual) != package["hash"]:
            raise LibraryError("Package checksum or file list does not match; do not install this incomplete or changed copy")
        return path

    def verify(self, selector) -> dict:
        entry = self.get(selector)
        if entry["kind"] == "plugin":
            return {"id": entry["id"], "status": "plugin_managed", "plugin": entry.get("plugin", {})}
        if not entry.get("package"):
            raise LibraryError("No shared standalone package is available")
        self._verify_package(entry["package"])
        return {"id": entry["id"], "status": "verified", "hash": entry["package"]["hash"]}

    def publish(self, source_dir: Path, metadata: dict, *, promote=False) -> dict:
        source_dir = Path(source_dir).absolute()
        if any(part.lower() in ARCHIVE_DIRS for part in source_dir.parts):
            raise LibraryError("Recovery archives are not import sources")
        details = read_skill(source_dir)
        if details["name"] in EXCLUDED_NAMES:
            raise LibraryError("This skill has been retired or removed: " + details["name"])
        metadata = deepcopy(metadata or {})
        if metadata.get("kind", "standalone") != "standalone":
            raise LibraryError("Plugin packages must remain with their original plugin installer")
        if metadata.get("name", details["name"]) != details["name"]:
            raise LibraryError("Catalogue name differs from the skill's actual name")
        source = metadata.get("source") or {"kind": "unknown"}
        identity = metadata.get("id") or entry_id("standalone", details["name"], source)
        if self._is_excluded(identity):
            raise LibraryError("This entry was removed from the shared library")
        old = next((item for item in self.entries() if item["id"] == identity), None)
        files = _files(source_dir)
        explicit_executables = metadata.get("portable_executables", [])
        if not isinstance(explicit_executables, list):
            raise LibraryError("portable_executables must be a list of relative file paths")
        files_by_path = {item["path"]: item for item in files}
        for executable in explicit_executables:
            _portable_relative(executable)
            if executable not in files_by_path:
                raise LibraryError("Declared executable is absent from the portable package: " + executable)
            files_by_path[executable]["executable"] = True
        digest = _digest(files)
        relative = "library/packages/" + identity + "/" + digest + "/skill"
        destination = _safe_child(self.root, relative)
        package = {"hash": digest, "path": relative, "files": files}
        self._validate({"id": identity, "name": details["name"], "kind": "standalone", "source": source, "package": package})
        if destination.exists():
            self._verify_package(package)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage = destination.parent / (".stage-" + uuid.uuid4().hex)
            stage.mkdir()
            try:
                for item in files:
                    target = _safe_child(stage, item["path"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(source_dir / item["path"]), str(target))
                if _digest(_files(stage, ignore_environments=False)) != digest:
                    raise LibraryError("Skill changed while it was being copied; retry from a stable source")
                os.replace(str(stage), str(destination))
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        if old and old.get("package", {}).get("hash") != digest and not promote:
            raise LibraryError("A different shared revision already exists; the new immutable copy is retained but not promoted")
        entry = deepcopy(old or {})
        entry.update({"name": details["name"], "description": details["description"], "version": details["version"], "kind": "standalone", "source": source})
        entry.update(metadata)
        entry.update({"id": identity, "package": package})
        entry.setdefault("requirements", [])
        entry.setdefault("accounts_costs", "Unknown; inspect the skill's requirements")
        entry.setdefault("platforms", [])
        versions = deepcopy(old.get("versions", []) if old else [])
        if not any(item.get("hash") == digest for item in versions):
            versions.append({"hash": digest, "path": relative, "version": entry["version"], "created_at": _now()})
        entry["versions"] = versions
        if old and entry == old:
            return old
        return self.save_entry(entry, expected=old)

    def install(self, selector, skills_root: Path, *, replace=False) -> dict:
        entry = self.get(selector)
        if entry["kind"] == "plugin":
            return {"action": "install_plugin", "plugin": deepcopy(entry.get("plugin", {})), "entry": entry}
        package = entry.get("package")
        if not package:
            raise LibraryError("No shared standalone package is available")
        source = self._verify_package(package)
        skills_root = Path(skills_root).absolute()
        if skills_root.exists() and _linked(skills_root):
            raise LibraryError("Choose a real local skill directory, not a linked directory")
        target = _safe_child(skills_root, entry["name"])
        if target.exists() and _linked(target):
            raise LibraryError("An existing linked skill must be resolved before installation")
        previous_hash = fingerprint(target) if target.exists() else None
        if previous_hash == package["hash"]:
            return {"id": entry["id"], "path": str(target), "hash": previous_hash, "status": "unchanged"}
        known = {version.get("hash") for version in entry.get("versions", [])}
        if previous_hash and previous_hash not in known and not replace:
            raise LibraryError("The local skill is modified or from another source; preserve it and choose replacement explicitly")
        token = uuid.uuid4().hex
        stage_root = skills_root.parent / ".shared-skills-library-staging" / token
        stage = stage_root / entry["name"]
        backup = skills_root.parent / "skill-backups" / "shared-skills-library" / token / entry["name"]
        backup_created = False
        try:
            shutil.copytree(str(source), str(stage))
            if os.name != "nt":
                for item in package["files"]:
                    if item.get("executable"):
                        executable = _safe_child(stage, item["path"])
                        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            if fingerprint(stage) != package["hash"]:
                raise LibraryError("Local staged copy failed verification")
            skills_root.mkdir(parents=True, exist_ok=True)
            if target.exists():
                # Another local writer changing the destination during staging is a conflict.
                if fingerprint(target) != previous_hash:
                    raise LibraryError("The local skill changed during installation; retry after reconciliation")
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(target), str(backup))
                backup_created = True
            os.replace(str(stage), str(target))
        except BaseException:
            if backup_created and not target.exists():
                os.replace(str(backup), str(target))
            raise
        finally:
            if stage_root.exists():
                shutil.rmtree(stage_root)
        result = {"id": entry["id"], "path": str(target), "hash": package["hash"], "status": "installed"}
        if backup_created:
            result["backup_path"] = str(backup)
        return result

    def archive_entry(self, selector) -> dict:
        entry = self.get(selector)
        identity = entry["id"]
        with self._write_lock(identity):
            current = next((item for item in self.entries() if item["id"] == identity), None)
            if current != entry:
                raise LibraryError("Catalogue changed before removal; reconcile first")
            archive = self.state / "archive" / identity / (uuid.uuid4().hex + ".json")
            archive.parent.mkdir(parents=True, exist_ok=True)
            tombstone = {"id": identity, "name": entry["name"], "kind": entry["kind"], "source": entry.get("source", {}), "removed_at": _now(), "reason": "Removed from the shared library"}
            self._write_json(self.exclusions / (identity + ".json"), tombstone)
            os.replace(str(self.entry_dir / (identity + ".json")), str(archive))
        return {"id": identity, "status": "archived", "archive_path": str(archive)}
