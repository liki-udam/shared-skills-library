"""Shared Skills Library v1.0.1: bounded read-only source update checks."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
import json
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")


class _SourceRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.hostname != "api.github.com" or parts.username or parts.password:
            raise ValueError("Unsupported source redirect")
        return super().redirect_request(request, response, code, message, headers, newurl)


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _public_url(value):
    if not isinstance(value, str) or not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        return None
    return urlunsplit((parts.scheme, parts.hostname, parts.path, "", ""))


def _fetch_json(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "api.github.com" or parts.username or parts.password:
        raise ValueError("Unsupported source endpoint")
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Shared-Skills-Library/1.0"})
    with build_opener(_SourceRedirects()).open(request, timeout=8) as response:
        final = urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname != "api.github.com":
            raise ValueError("Unexpected source redirect")
        body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError("Source response exceeded the check limit")
        return json.loads(body.decode("utf-8"))


def resolve_revision(source, revision, fetch_json=None):
    """Resolve a source tag/ref to an immutable commit without changing it."""
    if isinstance(revision, str) and len(revision) == 40 and _COMMIT.fullmatch(revision):
        return revision
    repository = source.get("repository")
    if (source.get("kind") != "github" or not _REPO.fullmatch(str(repository or ""))
            or any(part in {".", ".."} for part in str(repository).split("/"))):
        raise ValueError("Cannot resolve revision: a valid original GitHub repository is required")
    if (not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", revision)
            or any(part in {"", ".", ".."} for part in revision.split("/")) or ".." in revision):
        raise ValueError("Cannot resolve revision: use a source tag or complete commit, not a URL")
    endpoint = "https://api.github.com/repos/" + repository + "/commits/" + quote(revision, safe="")
    try:
        response = (fetch_json or _fetch_json)(endpoint)
        commit = response.get("sha") if isinstance(response, dict) else None
        if not isinstance(commit, str) or len(commit) != 40 or not _COMMIT.fullmatch(commit):
            raise ValueError("Original source did not resolve to a complete immutable commit")
        return commit
    except HTTPError as error:
        raise ValueError("Could not resolve original GitHub revision (HTTP " + str(error.code) + "); check source access and tag availability") from error
    except (OSError, URLError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError("Could not resolve the original source revision to a complete immutable commit; check source access and tag availability") from error


def _version(value):
    match = re.fullmatch(r"[vV]?(\d+(?:\.\d+)*)(?:\+[^\s]+)?", str(value or ""))
    if not match:
        return None
    numbers = [int(part) for part in match.group(1).split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


def _plugin_result(entry, result):
    source = entry.get("source") or {}
    plugin = entry.get("plugin") or {}
    present = plugin.get("version") or entry.get("version")
    latest = source.get("latest_version") or plugin.get("latest_version")
    result.update(current_revision=present, latest_revision=latest)
    if not latest:
        result.update(status="provider_managed", detail="The original plugin provider must report the available version; no newer version was verified by this check.")
    elif present == latest:
        result.update(status="current", detail="The installed plugin version matches the version reported by its original provider.")
    elif _version(present) is not None and _version(latest) is not None:
        if _version(latest) > _version(present):
            result.update(status="update_available", detail="The original provider reports a newer whole-plugin version.")
        else:
            result.update(status="current", detail="The installed version is at least as recent as the version reported by the provider.")
    else:
        result.update(status="source_changed", detail="The provider reports a different version; its ordering needs review.")
    return result


def check_source(entry, fetch_json=None):
    """Return status data; never install, upload, execute a package or buy access."""
    source = entry.get("source") or {}
    result = {"id": entry.get("id") or entry.get("name"), "checked_at": _now(),
              "status": "source_unknown", "current_revision": source.get("revision"),
              "latest_revision": None, "source_url": _public_url(source.get("url") or source.get("original_url")),
              "detail": "No checkable original source is recorded."}
    if entry.get("kind") == "plugin" or source.get("kind") == "marketplace":
        return _plugin_result(entry, result)
    if source.get("kind") in {"local", "custom"}:
        result.update(status="local_source", detail="This is a local/custom source; shared package comparisons are separate from remote publisher checks.")
        return result
    repository = source.get("repository")
    if source.get("kind") != "github" and not repository:
        return result
    fetch = fetch_json or _fetch_json
    try:
        if not _REPO.fullmatch(str(repository or "")):
            raise ValueError("Invalid GitHub repository identity")
        path = str(source.get("path") or ".").replace("\\", "/")
        if path.startswith("/") or ".." in path.split("/") or ":" in path:
            raise ValueError("Invalid source package path")
        base = "https://api.github.com/repos/" + repository
        result["source_url"] = "https://github.com/" + repository
        if source.get("tracking") == "release":
            latest_release = fetch(base + "/releases/latest")
            latest = latest_release.get("tag_name")
            if not latest:
                raise ValueError("No stable release was reported")
            present = source.get("version") or entry.get("version")
            result.update(current_revision=present, latest_revision=latest)
            if source.get("baseline_verified") is not True:
                result.update(status="baseline_unknown", detail="A stable release is available, but the shared package's original baseline has not been verified.")
            elif present == latest or (_version(present) is not None and _version(latest) is not None and _version(present) >= _version(latest)):
                result.update(status="current", detail="The package is at least as recent as the latest stable release.")
            elif _version(present) is not None and _version(latest) is not None:
                result.update(status="update_available", detail="A newer stable release is available from the original publisher.")
            else:
                result.update(status="source_changed", detail="The publisher reports a different release; version ordering needs review.")
            return result
        query = {"sha": source.get("ref") or "main", "per_page": 1}
        if path not in {"", "."}:
            query["path"] = path
        commits = fetch(base + "/commits?" + urlencode(query))
        if not isinstance(commits, list) or not commits or not _COMMIT.fullmatch(str(commits[0].get("sha", ""))):
            raise ValueError("No valid package revision was returned")
        latest = commits[0]["sha"]
        result["latest_revision"] = latest
        present = source.get("revision")
        if source.get("baseline_verified") is not True or not _COMMIT.fullmatch(str(present or "")):
            result.update(status="baseline_unknown", detail="The original source was checked, but no verified installed-source revision exists for comparison.")
        elif present.lower() == latest.lower():
            result.update(status="current", detail="The verified source revision matches the latest package revision.")
        else:
            comparison = fetch(base + "/compare/" + quote(present, safe="") + "..." + quote(latest, safe=""))
            relation = comparison.get("status") if isinstance(comparison, dict) else None
            if relation == "ahead":
                result.update(status="update_available", detail="The original package has changes newer than the verified installed-source revision.")
            elif relation in {"identical", "behind"}:
                result.update(status="current", detail="The latest package change is already included in the verified source revision.")
            elif relation == "diverged":
                result.update(status="source_changed", detail="Source history has diverged; review is needed before choosing an update.")
            else:
                raise ValueError("The source comparison did not establish revision ordering")
    except HTTPError as error:
        result.update(status="check_failed", detail="Original source check failed with HTTP " + str(error.code) + "; access or rate limits may prevent verification.")
    except (OSError, URLError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        result.update(status="check_failed", detail="Original source check could not be completed; the saved package has not been changed.")
    return result


def check_all(entries, fetch_json=None):
    """Check each entry, sharing only identical HTTP requests within this use."""
    fetch = fetch_json or _fetch_json
    pending = {}
    lock = threading.Lock()

    def shared_fetch(url):
        with lock:
            future = pending.get(url)
            owner = future is None
            if owner:
                future = Future()
                pending[url] = future
        if owner:
            try:
                future.set_result(fetch(url))
            except Exception as error:
                future.set_exception(error)
        return future.result()

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda entry: check_source(entry, shared_fetch), entries))
