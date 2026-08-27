import unittest

from agent_thanks.manifests import parse_manifest


class ManifestParsingTests(unittest.TestCase):
    def test_requirements(self) -> None:
        dependencies = parse_manifest(
            "requirements.txt",
            "requests>=2\ncustom @ git+https://github.com/acme/custom.git@main\n",
        )
        self.assertEqual([item.name for item in dependencies], ["requests", "custom"])
        self.assertEqual(dependencies[1].repository, "acme/custom")

    def test_pyproject_pep_621_and_poetry(self) -> None:
        dependencies = parse_manifest(
            "pyproject.toml",
            """
[project]
dependencies = ["httpx>=0.27"]

[project.optional-dependencies]
test = ["pytest>=8"]

[tool.poetry.dependencies]
python = "^3.10"
rich = { git = "https://github.com/Textualize/rich.git" }
""",
        )
        self.assertEqual({item.name for item in dependencies}, {"httpx", "pytest", "rich"})
        rich = next(item for item in dependencies if item.name == "rich")
        self.assertEqual(rich.repository, "Textualize/rich")

    def test_package_json(self) -> None:
        dependencies = parse_manifest(
            "package.json",
            '{"dependencies":{"tool":"github:owner/tool#v1"},"devDependencies":{"vitest":"^2"}}',
        )
        self.assertEqual({item.name for item in dependencies}, {"tool", "vitest"})
        tool = next(item for item in dependencies if item.name == "tool")
        self.assertEqual(tool.repository, "owner/tool")

    def test_cargo_git_dependency(self) -> None:
        dependencies = parse_manifest(
            "Cargo.toml",
            "[dependencies]\n"
            'local-lib = { git = "https://github.com/acme/local-lib" }\n'
            'serde = "1"\n',
        )
        local = next(item for item in dependencies if item.name == "local-lib")
        self.assertEqual(local.repository, "acme/local-lib")

    def test_go_mod_github_dependencies(self) -> None:
        dependencies = parse_manifest(
            "go.mod",
            "module example.com/app\n\n"
            "require github.com/spf13/cobra v1.8.1\n"
            "require (\n"
            "  github.com/stretchr/testify v1.9.0\n"
            "  golang.org/x/sync v0.8.0 // indirect\n"
            ")\n",
        )

        by_name = {dependency.name: dependency for dependency in dependencies}
        self.assertEqual(by_name["github.com/spf13/cobra"].repository, "spf13/cobra")
        self.assertEqual(
            by_name["github.com/stretchr/testify"].repository,
            "stretchr/testify",
        )
        self.assertNotIn("golang.org/x/sync", by_name)

    def test_gitmodules_repositories(self) -> None:
        dependencies = parse_manifest(
            ".gitmodules",
            '[submodule "vendor/demo"]\n'
            "  path = vendor/demo\n"
            "  url = git@github.com:acme/demo.git\n",
        )

        self.assertEqual(len(dependencies), 1)
        self.assertEqual(dependencies[0].repository, "acme/demo")

    def test_malformed_gitmodules_is_reported_as_a_parse_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid .gitmodules"):
            parse_manifest(".gitmodules", '[submodule "broken"\nurl = nope\n')


if __name__ == "__main__":
    unittest.main()
