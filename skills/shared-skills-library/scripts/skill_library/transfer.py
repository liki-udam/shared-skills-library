"""Shared Skills Library v1.0.1: download a complete, pinned GitHub skill."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import stat
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_MEMBERS = 20000
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REVISION = re.compile(r"^[0-9a-fA-F]{40}$")
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM" + str(n) for n in range(1, 10)} | {"LPT" + str(n) for n in range(1, 10)}


class _SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.hostname != "codeload.github.com" or parts.username or parts.password:
            raise ValueError("The archive source returned an unsupported redirect")
        return super().redirect_request(request, response, code, message, headers, newurl)


def _download_bytes(url):
    request = Request(url, headers={"User-Agent": "Shared-Skills-Library/1.0", "Accept": "application/zip"})
    with build_opener(_SafeRedirects()).open(request, timeout=20) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_DOWNLOAD_BYTES:
            raise ValueError("Archive exceeds the 25 MiB download limit")
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ValueError("Archive exceeds the 25 MiB download limit")
        return data


def _parts(value, *, directory=False):
    if not isinstance(value, str) or "\\" in value or "\x00" in value or value.startswith("/"):
        raise ValueError("Unsafe path in package archive")
    value = value[:-1] if directory and value.endswith("/") else value
    parts = tuple(value.split("/"))
    if (not parts or len(parts) > 64 or any(not part or part in {".", ".."} or ":" in part
            or len(part) > 255 or part.endswith((".", " "))
            or part.split(".", 1)[0].upper() in _RESERVED for part in parts)):
        raise ValueError("Unsafe path in package archive")
    return parts


def _validated_members(archive):
    infos = archive.infolist()
    if not infos or len(infos) > MAX_MEMBERS:
        raise ValueError("Archive has an unsupported number of members")
    result, roots, seen = [], set(), set()
    expanded = 0
    for member in infos:
        parts = _parts(member.orig_filename, directory=member.is_dir())
        roots.add(parts[0])
        key = "/".join(parts).casefold()
        if key in seen:
            raise ValueError("Archive contains duplicate or case-conflicting paths")
        seen.add(key)
        mode_type = stat.S_IFMT(member.external_attr >> 16)
        if mode_type not in {0, stat.S_IFREG, stat.S_IFDIR} or member.flag_bits & 1:
            raise ValueError("Archive contains a linked, special or encrypted member")
        if len(parts) == 1 and not member.is_dir():
            raise ValueError("Archive files must belong to one root directory")
        expanded += member.file_size
        if expanded > MAX_EXPANDED_BYTES:
            raise ValueError("Archive exceeds the expanded-size limit")
        result.append((member, parts))
    if len(roots) != 1:
        raise ValueError("Archive must have exactly one root directory")
    return result, next(iter(roots))


def _package_name(skill_bytes):
    try:
        content = skill_bytes.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("SKILL.md must be readable UTF-8 text") from error
    if content.startswith("---"):
        frontmatter = content.split("---", 2)[1]
        name = re.search(r"(?m)^name:\s*[\"']?([a-z0-9-]+)[\"']?\s*$", frontmatter)
        if name and len(name.group(1)) <= 64 and _SKILL_NAME.fullmatch(name.group(1)):
            return name.group(1)
    return "skill"


def download_github_skill(source, revision, destination, fetch_bytes=None):
    """Extract the selected skill only after validating the complete ZIP directory.

    The caller records the exact revision in its source record. Source package
    contents are not rewritten or executed, and no state outside destination is
    touched. A sibling `.skill-library-transfer-modes.json` records original ZIP
    executable paths for publication from filesystems that cannot retain Unix
    modes. Existing destination packages or mode metadata are never replaced.
    """
    repository = source.get("repository")
    if source.get("kind") != "github" or not _REPO.fullmatch(str(repository or "")):
        raise ValueError("Only a recorded GitHub repository can be transferred")
    if not _REVISION.fullmatch(str(revision or "")):
        raise ValueError("A complete immutable 40-character commit revision is required")
    selected = source.get("path") or "."
    selected_parts = () if selected == "." else _parts(selected)
    url = "https://codeload.github.com/" + repository + "/zip/" + quote(revision, safe="")
    data = (fetch_bytes or _download_bytes)(url)
    if not isinstance(data, bytes) or len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("Archive exceeds the download limit or is not bytes")
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, OSError) as error:
        raise ValueError("Source did not return a readable ZIP archive") from error
    with archive:
        members, root = _validated_members(archive)
        prefix = (root,) + selected_parts
        selected_files = {}
        root_licences = {}
        for member, parts in members:
            if member.is_dir():
                continue
            if parts[:len(prefix)] == prefix and len(parts) > len(prefix):
                selected_files[parts[len(prefix):]] = member
            if len(parts) == 2 and re.fullmatch(r"(?:LICEN[CS]E|COPYING)(?:\..+)?", parts[-1], re.IGNORECASE):
                root_licences[(parts[-1],)] = member
        if ("SKILL.md",) not in selected_files:
            raise ValueError("The selected source directory does not contain SKILL.md")
        # These paths were validated above and belong to the selected subtree.
        # A repository-level licence copied in below is supporting material,
        # never a source of executable hints for the selected skill.
        executables = sorted("/".join(parts) for parts, member in selected_files.items()
                             if (member.external_attr >> 16) & 0o111)
        if not any(len(parts) == 1 and re.fullmatch(r"(?:LICEN[CS]E|COPYING)(?:\..+)?", parts[0], re.IGNORECASE)
                   for parts in selected_files):
            selected_files.update(root_licences)
        skill_bytes = archive.read(selected_files[("SKILL.md",)])
        name = _package_name(skill_bytes)
        destination = Path(destination).expanduser()
        if destination.is_symlink():
            raise ValueError("Destination cannot be a symbolic link")
        destination.mkdir(parents=True, exist_ok=True)
        destination = destination.resolve()
        target = destination / name
        metadata_path = destination / ".skill-library-transfer-modes.json"
        if target.exists() or target.is_symlink():
            raise ValueError("Destination package already exists")
        if metadata_path.exists() or metadata_path.is_symlink():
            raise ValueError("Destination transfer metadata already exists")
        stage = Path(tempfile.mkdtemp(prefix=".skill-download-", dir=str(destination)))
        metadata_created = False
        try:
            written = 0
            for relative, member in selected_files.items():
                output = stage.joinpath(*relative)
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as incoming, output.open("xb") as outgoing:
                    while True:
                        chunk = incoming.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_EXPANDED_BYTES:
                            raise ValueError("Extracted package exceeds the expanded-size limit")
                        outgoing.write(chunk)
                mode = (member.external_attr >> 16) & 0o777
                if mode & 0o111:
                    output.chmod(0o755)
            if target.exists() or target.is_symlink():
                raise ValueError("Destination package appeared during transfer")
            with metadata_path.open("x", encoding="utf-8") as metadata_file:
                metadata_created = True
                json.dump({"executables": executables, "source_revision": revision,
                           "package": name}, metadata_file, indent=2)
                metadata_file.write("\n")
            stage.rename(target)
            return target
        except Exception:
            # stage was created under the already resolved explicit destination.
            if stage.exists() and stage.parent == destination:
                shutil.rmtree(stage)
            if metadata_created and metadata_path.parent == destination:
                metadata_path.unlink(missing_ok=True)
            raise
