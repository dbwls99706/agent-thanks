import unittest

from agent_thanks.manifests import Dependency, parse_manifest


class ManifestParsingTests(unittest.TestCase):
    def test_cargo_package_alias_uses_the_published_package_name(self) -> None:
        dependencies = parse_manifest("Cargo.toml", '[dependencies]\nserde_json={package="serde",version="1"}\n')
        self.assertEqual(dependencies[0].name, "serde")
        self.assertTrue(dependencies[0].from_registry)

    def test_source_overrides_do_not_claim_the_default_upstream(self) -> None:
        cases = [
            ("Cargo.toml", '[dependencies]\nserde="1"\n[patch.crates-io]\nserde={path="../local"}\n'),
            ("Cargo.toml", '[dependencies]\nserde="1"\n[patch.crates-io]\nalias={package="serde",git="https://github.com/fork/serde"}\n'),
            ("Cargo.toml", '[dependencies]\nserde={git="https://github.com/serde-rs/serde"}\n[replace]\n"serde:1.0.0"={path="../local"}\n'),
            ("pyproject.toml", '[project]\ndependencies=["requests"]\n[tool.uv.sources]\nRequests={path="../local"}\n'),
        ]
        for path, content in cases:
            with self.subTest(path=path, content=content):
                dependency = parse_manifest(path, content)[0]
                self.assertIsNone(dependency.repository)
                self.assertFalse(dependency.from_registry)

    def test_requirements_alternate_sources_preserve_only_explicit_repository_pins(self) -> None:
        for option in ("--index-url=https://private.example/simple", "--extra-index-url https://private.example/simple",
                       "--find-links ../wheels", "--no-index", "-ihttps://private.example/simple", "-f ../wheels"):
            with self.subTest(option=option):
                dependencies = parse_manifest(
                    "requirements.txt", "requests\n" + option + "\ncustom @ git+https://github.com/actual/custom.git\n"
                )
                self.assertFalse(dependencies[0].from_registry)
                self.assertIsNone(dependencies[0].repository)
                self.assertEqual(dependencies[1].repository, "actual/custom")

    def test_python_distribution_name_variations_share_an_identity(self) -> None:
        for name in ("zope.interface", "Zope_Interface", "zope--interface", "zope._-interface"):
            with self.subTest(name=name):
                self.assertEqual(Dependency("pypi", name).identity, Dependency("pypi", "zope-interface").identity)

    def test_go_replacements_identify_the_source_actually_used(self) -> None:
        for target, expected in (
            ("../local", None),
            ('"../local library"', None),
            ("github.com/local/directory", None),
            ("github.com/fork/lib v1.2.3", "fork/lib"),
            ("example.org/other/lib v1.2.3", None),
        ):
            with self.subTest(target=target):
                dependencies = parse_manifest(
                    "go.mod", "require github.com/original/lib v1.0.0\n"
                    f"replace github.com/original/lib => {target}\n"
                )
                self.assertEqual(dependencies[0].repository, expected)
                self.assertFalse(dependencies[0].from_registry)
                self.assertNotEqual(dependencies[0].repository, "original/lib")

    def test_go_version_specific_replacements_only_affect_matching_versions(self) -> None:
        text = (
            "replace (\n"
            " github.com/original/lib => github.com/general/lib v1.1.0\n"
            " github.com/original/lib v1.0.0 => github.com/specific/lib v1.2.0\n"
            ")\n"
            "require github.com/original/lib {version}\n"
        )
        for version, expected in (("v1.0.0", "specific/lib"), ("v2.0.0", "general/lib")):
            with self.subTest(version=version):
                self.assertEqual(parse_manifest("go.mod", text.format(version=version))[0].repository, expected)
        self.assertEqual(parse_manifest(
            "go.mod", "require github.com/original/lib v1.0.0\n"
            "replace github.com/original/lib v2.0.0 => ../unused\n"
        )[0].repository, "original/lib")

    def test_invalid_go_replacements_do_not_fall_back_to_upstream(self) -> None:
        for replacement in (
            "replace github.com/original/lib ../local\n",
            "replace (\ngithub.com/original/lib => ../local\n",
            "replace github.com/original/lib => ../one\nreplace github.com/original/lib => ../two\n",
        ):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                parse_manifest("go.mod", "require github.com/original/lib v1.0.0\n" + replacement)

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

    def test_editable_vcs_requirements_keep_their_repository(self) -> None:
        dependencies = parse_manifest(
            "requirements.txt",
            "-e git+https://github.com/acme/fork.git#egg=fork\n"
            "--editable git+ssh://git@github.com/acme/tool.git#egg=tool\n"
            "--editable=git+https://github.com/acme/lib.git@v2#egg=lib\n"
            "-egit+https://github.com/acme/attached.git#egg=attached\n"
            "-e .\n"
            "-e ./vendor/local[dev]\n",
        )
        by_name = {item.name: item for item in dependencies}
        self.assertEqual(set(by_name), {"fork", "tool", "lib", "attached"})
        self.assertEqual(by_name["fork"].repository, "acme/fork")
        self.assertEqual(by_name["tool"].repository, "acme/tool")
        self.assertEqual(by_name["lib"].repository, "acme/lib")
        self.assertEqual(by_name["attached"].repository, "acme/attached")
        self.assertFalse(any(item.from_registry for item in dependencies))

    def test_pinned_requirement_sources_are_not_registry_packages(self) -> None:
        dependencies = parse_manifest(
            "requirements.txt",
            "requests>=2\n"
            "custom[extra] @ git+https://github.com/acme/custom.git\n"
            "git+https://gitlab.com/acme/private.git#egg=private\n"
            "https://github.com/acme/archive/archive/main.zip\n",
        )
        by_name = {item.name: item for item in dependencies}
        self.assertTrue(by_name["requests"].from_registry)
        self.assertFalse(by_name["custom"].from_registry)
        self.assertEqual(by_name["custom"].repository, "acme/custom")
        self.assertFalse(by_name["private"].from_registry)
        self.assertIsNone(by_name["private"].repository)
        self.assertFalse(by_name["acme/archive"].from_registry)
        self.assertEqual(by_name["acme/archive"].repository, "acme/archive")

    def test_local_and_url_specs_skip_registry_lookup(self) -> None:
        npm = parse_manifest(
            "package.json",
            '{"dependencies":{"shared":"file:../shared","left-pad":"^1"}}',
        )
        by_name = {item.name: item for item in npm}
        self.assertFalse(by_name["shared"].from_registry)
        self.assertTrue(by_name["left-pad"].from_registry)

        cargo = parse_manifest(
            "Cargo.toml",
            '[dependencies]\ncore = { path = "../core" }\nserde = "1"\n',
        )
        by_name = {item.name: item for item in cargo}
        self.assertFalse(by_name["core"].from_registry)
        self.assertTrue(by_name["serde"].from_registry)

        poetry = parse_manifest(
            "pyproject.toml",
            '[tool.poetry.dependencies]\nhelper = { path = "../helper" }\nrich = "^13"\n',
        )
        by_name = {item.name: item for item in poetry}
        self.assertFalse(by_name["helper"].from_registry)
        self.assertTrue(by_name["rich"].from_registry)

    def test_pip_option_lines_are_ignored(self) -> None:
        dependencies = parse_manifest(
            "requirements.txt",
            "--index-url https://pypi.example.com/simple\n"
            "--extra-index-url https://github.com/acme/index/simple\n"
            "-i https://mirror.example.com/simple\n"
            "-f https://github.com/acme/wheels/releases\n"
            "--find-links=https://example.com/links\n"
            "--trusted-host mirror.example.com\n"
            "-r other.txt\n"
            "-c constraints.txt\n"
            "requests>=2\n",
        )
        self.assertEqual([item.name for item in dependencies], ["requests"])

    def test_local_path_requirements_are_not_github_shorthand(self) -> None:
        for line in ("vendor/pkg", "vendor/pkg[extra]", "./vendor/pkg", "../shared", "/opt/pkg", "~/pkg"):
            with self.subTest(line=line):
                self.assertEqual(parse_manifest("requirements.txt", line + "\n"), [])

        npm = parse_manifest("package.json", '{"dependencies":{"tool":"owner/tool"}}')
        self.assertEqual(npm[0].repository, "owner/tool")

    def test_identity_distinguishes_pinned_repository_sources(self) -> None:
        registry, pinned = parse_manifest(
            "requirements.txt",
            "fork-lib>=1\nfork_lib @ git+https://github.com/someone/fork-lib.git\n",
        )
        self.assertEqual(registry.identity[:2], pinned.identity[:2])
        self.assertNotEqual(registry.identity, pinned.identity)

    def test_malformed_gitmodules_is_reported_as_a_parse_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid .gitmodules"):
            parse_manifest(".gitmodules", '[submodule "broken"\nurl = nope\n')


if __name__ == "__main__":
    unittest.main()
