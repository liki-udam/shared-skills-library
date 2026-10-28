"""Read-only update checks, with network calls replaced at the HTTP boundary."""
import importlib
from pathlib import Path
import sys
import unittest
from urllib.error import HTTPError

SCRIPTS = Path(__file__).resolve().parents[1] / "library/skills/shared-skills-library/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class UpdateTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("skill_library.updates")
        except ModuleNotFoundError:
            self.fail("The update adapter must exist before sources can be checked")

    def entry(self, verified=True, revision=None):
        return {"id": "standalone-example", "name": "example", "kind": "standalone",
                "source": {"kind": "github", "repository": "owner/repo", "path": "skills/example",
                           "ref": "main", "revision": revision or "a" * 40, "baseline_verified": verified}}

    def test_matching_verified_revision_is_current(self):
        result = self.module().check_source(self.entry(), lambda url: [{"sha": "a" * 40}])
        self.assertEqual(result["status"], "current")
        self.assertIn("checked_at", result)

    def test_changed_path_ahead_of_baseline_is_update_available(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return {"status": "ahead"} if "/compare/" in url else [{"sha": "b" * 40}]
        result = self.module().check_source(self.entry(), fetch)
        self.assertEqual(result["status"], "update_available")
        self.assertTrue(any("path=skills%2Fexample" in url for url in urls))
        self.assertEqual(result["latest_revision"], "b" * 40)

    def test_older_last_path_commit_does_not_mean_an_update(self):
        def fetch(url):
            return {"status": "behind"} if "/compare/" in url else [{"sha": "b" * 40}]
        self.assertEqual(self.module().check_source(self.entry(), fetch)["status"], "current")

    def test_diverged_source_requires_review(self):
        def fetch(url):
            return {"status": "diverged"} if "/compare/" in url else [{"sha": "b" * 40}]
        self.assertEqual(self.module().check_source(self.entry(), fetch)["status"], "source_changed")

    def test_historical_claim_never_establishes_current_or_outdated(self):
        for sha in ("a" * 40, "b" * 40):
            result = self.module().check_source(self.entry(verified=False), lambda url: [{"sha": sha}])
            self.assertEqual(result["status"], "baseline_unknown")

    def test_string_false_does_not_authorise_a_verified_baseline(self):
        entry = self.entry(verified="false")
        result = self.module().check_source(entry, lambda url: [{"sha": "a" * 40}])
        self.assertEqual(result["status"], "baseline_unknown")

    def test_release_tracking_compares_stable_versions(self):
        entry = self.entry()
        entry["version"] = "1.2.0"
        entry["source"]["tracking"] = "release"
        result = self.module().check_source(entry, lambda url: {"tag_name": "v1.3.0"})
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["latest_revision"], "v1.3.0")

    def test_network_failure_is_recorded_and_does_not_abort_batch(self):
        def fetch(url):
            raise HTTPError(url, 403, "rate limited", {}, None)
        entries = [self.entry(), {"id": "custom", "source": {"kind": "local"}}]
        results = self.module().check_all(entries, fetch)
        self.assertEqual([item["status"] for item in results], ["check_failed", "local_source"])
        self.assertNotIn("current", results[0]["detail"].lower())

    def test_unknown_source_and_marketplace_without_catalogue_are_explicit(self):
        result = self.module().check_source({"id": "unknown", "source": {"kind": "unknown"}})
        self.assertEqual(result["status"], "source_unknown")
        result = self.module().check_source({"id": "plugin-one", "kind": "plugin",
            "source": {"kind": "marketplace"}, "plugin": {"name": "one", "version": "1.0.0"}})
        self.assertEqual(result["status"], "provider_managed")

    def test_marketplace_new_version_is_reported_without_installation(self):
        result = self.module().check_source({"id": "plugin-one", "kind": "plugin",
            "source": {"kind": "marketplace", "latest_version": "1.1.0"},
            "plugin": {"name": "one", "version": "1.0.0"}})
        self.assertEqual(result["status"], "update_available")

    def test_batch_deduplicates_same_original_source_requests(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return [{"sha": "a" * 40}]
        other = self.entry()
        other["id"] = "another-package-copy"
        results = self.module().check_all([self.entry(), other], fetch)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(urls), 1)

    def test_unsafe_repository_cannot_redirect_a_request(self):
        entry = self.entry()
        entry["source"]["repository"] = "https://user:secret@evil.invalid/x"
        def fail_fetch(url):
            self.fail("Unsafe repository must not cause an HTTP request")
        result = self.module().check_source(entry, fail_fetch)
        self.assertEqual(result["status"], "check_failed")
        self.assertNotIn("secret", result["detail"])

    def test_resolve_full_commit_does_not_fetch(self):
        def fetch(url):
            self.fail("An immutable full commit must not need resolution")
        self.assertEqual(self.module().resolve_revision({}, "a" * 40, fetch), "a" * 40)

    def test_resolve_release_tag_uses_original_repo_and_returns_immutable_commit(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return {"sha": "b" * 40}
        revision = self.module().resolve_revision(
            {"kind": "github", "repository": "owner/repo"}, "release/v1.2.0", fetch)
        self.assertEqual(revision, "b" * 40)
        self.assertEqual(urls, ["https://api.github.com/repos/owner/repo/commits/release%2Fv1.2.0"])

    def test_resolve_rejects_invalid_repository_tag_and_returned_commit(self):
        for source, revision in (
            ({"kind": "github", "repository": "https://evil.invalid/repo"}, "v1"),
            ({"kind": "local", "repository": "owner/repo"}, "v1"),
            ({"kind": "github", "repository": "owner/.."}, "v1"),
            ({"kind": "github", "repository": "owner/repo"}, "https://evil.invalid/tag"),
        ):
            with self.subTest(source=source, revision=revision):
                def fetch(url):
                    self.fail("Unsupported revision source must be rejected before a request")
                with self.assertRaises(ValueError):
                    self.module().resolve_revision(source, revision, fetch)
        with self.assertRaises(ValueError):
            self.module().resolve_revision({"kind": "github", "repository": "owner/repo"}, "v1", lambda url: {"sha": "not-a-commit"})

    def test_resolve_source_failure_is_actionable_and_does_not_change_source(self):
        source = {"kind": "github", "repository": "owner/repo"}
        def fetch(url):
            raise HTTPError(url, 404, "not found", {}, None)
        with self.assertRaisesRegex(ValueError, "resolve"):
            self.module().resolve_revision(source, "v1", fetch)
        self.assertEqual(source, {"kind": "github", "repository": "owner/repo"})


if __name__ == "__main__":
    unittest.main()
