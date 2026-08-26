import unittest

from agent_thanks.resolver import PackageRepositoryResolver


class ResolverTests(unittest.TestCase):
    def test_resolves_pypi_preferred_source_url(self) -> None:
        resolver = PackageRepositoryResolver(
            fetcher=lambda _: {
                "info": {
                    "project_urls": {
                        "Homepage": "https://example.com",
                        "Source": "https://github.com/psf/requests",
                    }
                }
            }
        )
        self.assertEqual(resolver.resolve("pypi", "requests"), "psf/requests")

    def test_resolves_npm_latest_metadata(self) -> None:
        resolver = PackageRepositoryResolver(
            fetcher=lambda _: {
                "dist-tags": {"latest": "1.0.0"},
                "versions": {
                    "1.0.0": {"repository": "git+https://github.com/acme/pkg.git"}
                },
            }
        )
        self.assertEqual(resolver.resolve("npm", "pkg"), "acme/pkg")

    def test_offline_never_fetches(self) -> None:
        resolver = PackageRepositoryResolver(
            offline=True,
            fetcher=lambda _: self.fail("fetcher should not be called"),
        )
        self.assertIsNone(resolver.resolve("pypi", "requests"))


if __name__ == "__main__":
    unittest.main()
