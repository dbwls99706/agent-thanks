from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_thanks.models import Evidence
from agent_thanks.resolver import PackageRepositoryResolver
from agent_thanks.scanner import ProjectScanner


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class ScannerTests(unittest.TestCase):
    def session_evidence(self, text: str) -> dict[str, Evidence]:
        return {
            repository: evidence
            for repository, evidence in ProjectScanner._scan_session(text, "session.log")
        }

    def test_scans_new_dependency_and_session_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "config", "user.name", "Test")
            git(root, "config", "user.email", "test@example.com")
            manifest = root / "package.json"
            manifest.write_text('{"dependencies":{}}\n', encoding="utf-8")
            git(root, "add", "package.json")
            git(root, "commit", "-m", "base")

            manifest.write_text(
                '{"dependencies":{"robot-lib":"github:robotics/robot-lib"}}\n',
                encoding="utf-8",
            )
            session = root / "session.log"
            session.write_text(
                "git clone https://github.com/BehaviorTree/BehaviorTree.CPP.git\n"
                "Viewed https://github.com/example/read-only\n",
                encoding="utf-8",
            )

            report = ProjectScanner(
                root,
                resolver=PackageRepositoryResolver(offline=True),
            ).scan([session])
            by_name = {item.repository: item for item in report.candidates}

            self.assertTrue(by_name["robotics/robot-lib"].recommended)
            self.assertTrue(by_name["BehaviorTree/BehaviorTree.CPP"].recommended)
            self.assertFalse(by_name["example/read-only"].recommended)

    def test_session_command_promotes_only_its_repository_target(self) -> None:
        evidence = self.session_evidence(
            "git clone https://github.com/real/used.git  "
            "# background: https://github.com/unrelated/reference\n"
        )

        self.assertTrue(evidence["real/used"].meaningful)
        self.assertEqual(evidence["real/used"].confidence, "high")
        self.assertFalse(evidence["unrelated/reference"].meaningful)
        self.assertEqual(evidence["unrelated/reference"].confidence, "low")

    def test_session_reference_before_command_stays_low(self) -> None:
        evidence = self.session_evidence(
            "Compared https://github.com/reference/only; "
            "git clone https://github.com/real/used.git\n"
        )

        self.assertFalse(evidence["reference/only"].meaningful)
        self.assertTrue(evidence["real/used"].meaningful)

    def test_git_clone_option_url_is_not_the_repository_target(self) -> None:
        evidence = self.session_evidence(
            "git clone --reference https://github.com/cache/reference.git "
            "--recurse-submodules=vendor/* "
            "https://github.com/real/used.git\n"
        )

        self.assertFalse(evidence["cache/reference"].meaningful)
        self.assertTrue(evidence["real/used"].meaningful)

    def test_package_option_values_are_not_repository_targets(self) -> None:
        evidence = self.session_evidence(
            "pip install --constraint "
            "git+https://github.com/reference/constraints.git actual-package\n"
            "pip install --find-links "
            "git+https://github.com/reference/wheels.git actual-package\n"
            "uv add --index "
            "git+https://github.com/reference/index.git actual-package\n"
            "go get -modfile github.com/reference/go.mod github.com/real/pkg\n"
        )

        for repository in (
            "reference/constraints",
            "reference/wheels",
            "reference/index",
            "reference/go.mod",
        ):
            self.assertFalse(evidence[repository].meaningful, repository)
        self.assertTrue(evidence["real/pkg"].meaningful)

    def test_go_get_requires_a_module_path_not_a_transport_url(self) -> None:
        evidence = self.session_evidence(
            "go get https://github.com/reference/go-http\n"
            "go get git+https://github.com/reference/go-git-plus\n"
            "go get ssh://git@github.com/reference/go-ssh\n"
            "go get github.com/real/go/pkg@latest\n"
        )

        self.assertFalse(evidence["reference/go-http"].meaningful)
        self.assertFalse(evidence["reference/go-git-plus"].meaningful)
        self.assertFalse(evidence["reference/go-ssh"].meaningful)
        self.assertTrue(evidence["real/go"].meaningful)

    def test_only_direct_github_command_targets_are_promoted(self) -> None:
        evidence = self.session_evidence(
            "git clone "
            "https://example.com/redirect?next=https://github.com/victim/clone\n"
            "pip install "
            "git+https://example.com/r?src=https://github.com/victim/pip\n"
            "go get https://example.com/github.com/victim/go\n"
            "git clone https://github.com/real/target.git.\n"
        )

        self.assertFalse(evidence["victim/clone"].meaningful)
        self.assertFalse(evidence["victim/pip"].meaningful)
        self.assertNotIn("victim/go", evidence)
        self.assertTrue(evidence["real/target"].meaningful)
        self.assertNotIn("real/target.git", evidence)

    def test_repository_ui_subpaths_and_wrapped_examples_stay_low(self) -> None:
        evidence = self.session_evidence(
            "git clone https://github.com/reference/tree/tree/main\n"
            "git submodule add https://github.com/reference/issues/issues/1 vendor/x\n"
            "gh repo clone https://github.com/reference/gh/tree/main\n"
            "pip install git+https://github.com/reference/pip/tree/main\n"
            "git clone <https://github.com/reference/autolink>\n"
            "git clone (https://github.com/reference/parenthesized)\n"
            "> git clone https://github.com/reference/blockquote\n"
            "> ```sh\n"
            "> git clone https://github.com/reference/quoted-fence\n"
        )

        self.assertTrue(evidence)
        self.assertTrue(all(not item.meaningful for item in evidence.values()))

    def test_bare_github_hosts_are_only_valid_go_module_paths(self) -> None:
        evidence = self.session_evidence(
            "git clone github.com/reference/clone\n"
            "git submodule add github.com/reference/submodule vendor/x\n"
            "cargo add demo --git github.com/reference/cargo\n"
            "npm install github.com/reference/npm\n"
            "pnpm add github.com/reference/pnpm\n"
            "yarn add github.com/reference/yarn\n"
            "go get github.com/real/go/pkg@latest\n"
        )

        for repository, item in evidence.items():
            if repository == "real/go":
                self.assertTrue(item.meaningful)
            else:
                self.assertFalse(item.meaningful, repository)

    def test_git_plus_schemes_are_only_valid_package_operands(self) -> None:
        evidence = self.session_evidence(
            "git clone git+https://github.com/reference/clone\n"
            "git submodule add git+https://github.com/reference/submodule vendor/x\n"
            "gh repo clone git+https://github.com/reference/gh\n"
            "cargo add demo --git git+https://github.com/reference/cargo\n"
            "pip install demo@git+https://github.com/real/pip.git\n"
        )

        for repository, item in evidence.items():
            if repository == "real/pip":
                self.assertTrue(item.meaningful)
            else:
                self.assertFalse(item.meaningful, repository)

    def test_http_targets_reject_package_style_at_revisions(self) -> None:
        evidence = self.session_evidence(
            "git clone https://github.com/reference/clone.git@main\n"
            "git submodule add "
            "https://github.com/reference/submodule.git@main vendor/x\n"
            "gh repo clone https://github.com/reference/gh.git@main\n"
            "cargo add demo --git https://github.com/reference/cargo.git@main\n"
            "npm install https://github.com/reference/npm.git@main\n"
            "pip install git+https://github.com/real/pip.git@main\n"
            "go get github.com/real/go@v1.0.0\n"
        )

        for repository, item in evidence.items():
            if repository in {"real/pip", "real/go"}:
                self.assertTrue(item.meaningful)
            else:
                self.assertFalse(item.meaningful, repository)

    def test_conditional_followup_requires_a_known_reachable_status(self) -> None:
        evidence = self.session_evidence(
            "git clone https://github.com/first/used.git && "
            "git submodule add git@github.com:second/used.git vendor/second\n"
            "true && git clone https://github.com/known/and.git\n"
            "false || git clone https://github.com/known/or.git\n"
        )

        self.assertTrue(evidence["first/used"].meaningful)
        self.assertFalse(evidence["second/used"].meaningful)
        self.assertTrue(evidence["known/and"].meaningful)
        self.assertTrue(evidence["known/or"].meaningful)

    def test_literal_shell_conditions_do_not_promote_unreachable_commands(self) -> None:
        evidence = self.session_evidence(
            "false && git clone https://github.com/reference/and.git\n"
            "true || git clone https://github.com/reference/or.git\n"
            "false && git clone https://github.com/reference/skipped.git || "
            "git clone https://github.com/real/fallback.git\n"
            "test -f /tmp/x && git clone https://github.com/reference/test.git\n"
            "grep -q x file || git clone https://github.com/reference/grep.git\n"
            "unknown && git clone https://github.com/reference/unknown.git\n"
            "! true && git clone https://github.com/reference/negated.git\n"
            "false && echo x | git clone https://github.com/reference/and-pipe.git\n"
            "true || echo x | git clone https://github.com/reference/or-pipe.git\n"
            "exit 0; git clone https://github.com/reference/exit.git\n"
            "exec /bin/true; git clone https://github.com/reference/exec.git\n"
        )

        self.assertFalse(evidence["reference/and"].meaningful)
        self.assertFalse(evidence["reference/or"].meaningful)
        self.assertFalse(evidence["reference/skipped"].meaningful)
        self.assertFalse(evidence["reference/test"].meaningful)
        self.assertFalse(evidence["reference/grep"].meaningful)
        self.assertFalse(evidence["reference/unknown"].meaningful)
        self.assertFalse(evidence["reference/negated"].meaningful)
        self.assertFalse(evidence["reference/and-pipe"].meaningful)
        self.assertFalse(evidence["reference/or-pipe"].meaningful)
        self.assertFalse(evidence["reference/exit"].meaningful)
        self.assertFalse(evidence["reference/exec"].meaningful)
        self.assertTrue(evidence["real/fallback"].meaningful)

    def test_provenance_promotes_only_the_repository_after_the_marker(self) -> None:
        evidence = self.session_evidence(
            "Docs https://github.com/reference/before\n"
            "adapted from https://github.com/real/source\n"
            "background https://github.com/reference/after\n"
        )

        self.assertFalse(evidence["reference/before"].meaningful)
        self.assertTrue(evidence["real/source"].meaningful)
        self.assertFalse(evidence["reference/after"].meaningful)

    def test_negated_provenance_and_quoted_commands_stay_low(self) -> None:
        evidence = self.session_evidence(
            "This was not adapted from https://github.com/reference/negated\n"
            "Adapted from an internal source; viewed "
            "https://github.com/reference/later\n"
            'echo "git clone https://github.com/reference/quoted"\n'
            'echo "adapted from https://github.com/reference/provenance"\n'
            "curl https://github.com/reference/curl "
            "# git clone https://github.com/reference/comment\n"
            "# adapted from https://github.com/reference/full-comment\n"
            "echo ok # adapted from https://github.com/reference/tail-comment\n"
        )

        self.assertTrue(evidence)
        self.assertTrue(all(not item.meaningful for item in evidence.values()))

    def test_ambiguous_provenance_language_stays_low(self) -> None:
        evidence = self.session_evidence(
            "This was not, under any circumstances whatsoever, adapted from "
            "https://github.com/reference/long-negation\n"
            "No code was copied from https://github.com/reference/no\n"
            "Nothing was copied from https://github.com/reference/nothing\n"
            "Neither implementation was adapted from "
            "https://github.com/reference/neither\n"
            "We avoided code copied from https://github.com/reference/avoided\n"
            "Was this adapted from https://github.com/reference/question?\n"
            "I cannot confirm whether this was adapted from "
            "https://github.com/reference/uncertain\n"
            "This could have been copied from https://github.com/reference/maybe\n"
            "echo adapted from https://github.com/reference/echo\n"
            "Adapted from an internal source, viewed "
            "https://github.com/reference/later\n"
            "Adapted from memory while reviewing "
            "https://github.com/reference/memory\n"
            "adapted from https://github.com/reference/comma,maybe\n"
            "adapted from https://github.com/reference/hash "
            "# not actually used\n"
            "adapted from https://github.com/reference/semicolon; "
            "not actually used\n"
            "adapted from https://github.com/reference/period. "
            "Not actually used.\n"
        )

        self.assertTrue(evidence)
        self.assertTrue(all(not item.meaningful for item in evidence.values()))

    def test_explicit_provenance_requires_an_immediate_repository(self) -> None:
        evidence = self.session_evidence(
            "- adapted from: <https://github.com/real/provenance>\n"
        )

        self.assertTrue(evidence["real/provenance"].meaningful)

    def test_malformed_or_unknown_commands_fail_closed(self) -> None:
        evidence = self.session_evidence(
            'git clone --unknown https://github.com/reference/option "unterminated\n'
            "git clone https://[github.com/reference/bracket\n"
            "pip install git+https://[github.com/reference/pip-bracket\n"
            "git clone https://github.com:bad/reference/port\n"
            "git clone https://github.com/reference/trailing-and.git &&\n"
            "git clone https://github.com/reference/trailing-or.git ||\n"
            "git clone https://github.com/reference/trailing-pipe.git |\n"
        )

        self.assertTrue(evidence)
        self.assertTrue(all(not item.meaningful for item in evidence.values()))

    def test_trailing_unknown_options_and_extra_operands_fail_closed(self) -> None:
        evidence = self.session_evidence(
            "git clone https://github.com/reference/clone.git --unknown\n"
            "git clone https://github.com/reference/extra.git dir extra\n"
            "git submodule add https://github.com/reference/submodule.git --unknown\n"
            "gh repo clone https://github.com/reference/gh.git --unknown\n"
        )

        self.assertTrue(evidence)
        self.assertTrue(all(not item.meaningful for item in evidence.values()))

    def test_supported_package_commands_promote_only_explicit_sources(self) -> None:
        evidence = self.session_evidence(
            "pip install demo@git+https://github.com/pip/used.git "
            "# https://github.com/pip/reference\n"
            "cargo add demo --git=https://github.com/cargo/used.git\n"
            "go get github.com/go/one/pkg@latest github.com/go/two@v1.0.0\n"
            "npm install github:npm/used\n"
            "npm install --registry https://github.com/npm/reference "
            "--save-dev https://github.com/npm/direct.git\n"
            "gh repo clone gh/used\n"
        )

        for repository in (
            "pip/used",
            "cargo/used",
            "go/one",
            "go/two",
            "npm/direct",
            "npm/used",
            "gh/used",
        ):
            self.assertTrue(evidence[repository].meaningful, repository)
        self.assertFalse(evidence["pip/reference"].meaningful)
        self.assertFalse(evidence["npm/reference"].meaningful)

    def test_non_mutating_package_commands_stay_low(self) -> None:
        evidence = self.session_evidence(
            "pip install --dry-run "
            "demo@git+https://github.com/dry/pip.git\n"
            "uv add --dry-run git+https://github.com/dry/uv.git\n"
            "cargo add demo --dry-run --git https://github.com/dry/cargo.git\n"
            "npm install --dry-run https://github.com/dry/npm.git\n"
            "pnpm add --dry-run https://github.com/dry/pnpm.git\n"
            "yarn add --dry-run https://github.com/dry/yarn.git\n"
            "go get -n github.com/dry/go\n"
            "go get github.com/remove/module@none\n"
            "go get github.com/remove/dotted@none.\n"
            "go get github.com/remove/comma@none,\n"
            "cargo add --help demo --git https://github.com/dry/cargo-help.git\n"
            "cargo add --git https://github.com/dry/cargo-no-package.git\n"
            "cargo add demo --config --git "
            "https://github.com/dry/cargo-option-value.git\n"
        )

        self.assertTrue(evidence)
        self.assertTrue(all(not item.meaningful for item in evidence.values()))

    def test_fenced_and_heredoc_command_examples_stay_low(self) -> None:
        evidence = self.session_evidence(
            "```sh\n"
            "git clone https://github.com/reference/fenced.git\n"
            "```\n"
            "cat <<'EOF'\n"
            "git clone https://github.com/reference/heredoc.git\n"
            "EOF\n"
            "    git clone https://github.com/reference/indented.git\n"
            "cat <<\\ESCAPED\n"
            "git clone https://github.com/reference/escaped.git\n"
            "ESCAPED\n"
            "cat <<123\n"
            "git clone https://github.com/reference/numeric.git\n"
            "123\n"
            "cat <<A <<B\n"
            "git clone https://github.com/reference/heredoc-a.git\n"
            "A\n"
            "git clone https://github.com/reference/heredoc-b.git\n"
            "B\n"
            "cat <<$'DOLLAR'\n"
            "git clone https://github.com/reference/heredoc-dollar.git\n"
            "DOLLAR\n"
            "cat <<@END\n"
            "git clone https://github.com/reference/heredoc-at.git\n"
            "@END\n"
            "cat <<END+END\n"
            "git clone https://github.com/reference/heredoc-plus.git\n"
            "END+END\n"
            "git clone https://github.com/real/executed.git\n"
        )

        self.assertFalse(evidence["reference/fenced"].meaningful)
        self.assertFalse(evidence["reference/heredoc"].meaningful)
        self.assertFalse(evidence["reference/indented"].meaningful)
        self.assertFalse(evidence["reference/escaped"].meaningful)
        self.assertFalse(evidence["reference/numeric"].meaningful)
        self.assertFalse(evidence["reference/heredoc-a"].meaningful)
        self.assertFalse(evidence["reference/heredoc-b"].meaningful)
        self.assertFalse(evidence["reference/heredoc-dollar"].meaningful)
        self.assertFalse(evidence["reference/heredoc-at"].meaningful)
        self.assertFalse(evidence["reference/heredoc-plus"].meaningful)
        self.assertTrue(evidence["real/executed"].meaningful)

    def test_malformed_gh_long_option_fails_closed(self) -> None:
        evidence = self.session_evidence(
            "gh repo clone --upstream-remote-namefoo reference/option\n"
        )

        self.assertNotIn("reference/option", evidence)

    def test_repeated_target_and_reference_produce_one_evidence_item(self) -> None:
        items = ProjectScanner._scan_session(
            "git clone https://github.com/owner/repo.git "
            "# docs https://github.com/owner/repo\n",
            "session.log",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][0], "owner/repo")
        self.assertTrue(items[0][1].meaningful)

    def test_line_continuation_preserves_the_command_target(self) -> None:
        evidence = self.session_evidence(
            "git clone \\\n"
            "  https://github.com/continued/used.git\n"
        )

        self.assertTrue(evidence["continued/used"].meaningful)
        self.assertEqual(evidence["continued/used"].source, "session.log:1")

    def test_non_git_project_scans_current_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requirements.txt").write_text(
                "demo @ git+https://github.com/acme/demo.git\n", encoding="utf-8"
            )
            report = ProjectScanner(
                root,
                resolver=PackageRepositoryResolver(offline=True),
            ).scan()
            self.assertEqual(report.base, None)
            self.assertEqual(report.candidates[0].repository, "acme/demo")

    def test_unborn_git_repository_scans_current_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            (root / "requirements.txt").write_text(
                "demo @ git+https://github.com/acme/demo.git\n", encoding="utf-8"
            )
            report = ProjectScanner(
                root,
                resolver=PackageRepositoryResolver(offline=True),
            ).scan()
            self.assertIsNone(report.base)
            self.assertEqual(report.candidates[0].repository, "acme/demo")

    def test_pure_manifest_rename_does_not_recommend_existing_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "config", "user.name", "Test")
            git(root, "config", "user.email", "test@example.com")
            manifest = root / "package.json"
            manifest.write_text(
                '{"dependencies":{"tool":"github:owner/tool"}}\n',
                encoding="utf-8",
            )
            git(root, "add", "package.json")
            git(root, "commit", "-m", "base")

            frontend = root / "frontend"
            frontend.mkdir()
            git(root, "mv", "package.json", "frontend/package.json")

            report = ProjectScanner(
                root,
                resolver=PackageRepositoryResolver(offline=True),
            ).scan()

            self.assertEqual(report.candidates, [])

    def test_manifest_rename_reports_only_dependencies_added_after_the_move(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "config", "user.name", "Test")
            git(root, "config", "user.email", "test@example.com")
            manifest = root / "package.json"
            manifest.write_text(
                '{"dependencies":{"existing":"github:owner/existing"}}\n',
                encoding="utf-8",
            )
            git(root, "add", "package.json")
            git(root, "commit", "-m", "base")

            frontend = root / "frontend"
            frontend.mkdir()
            git(root, "mv", "package.json", "frontend/package.json")
            (frontend / "package.json").write_text(
                "{\"dependencies\":{"
                "\"existing\":\"github:owner/existing\","
                "\"added\":\"github:owner/added\"}}\n",
                encoding="utf-8",
            )

            report = ProjectScanner(
                root,
                resolver=PackageRepositoryResolver(offline=True),
            ).scan()

            self.assertEqual(
                [candidate.repository for candidate in report.candidates],
                ["owner/added"],
            )

    def test_scans_new_go_module_without_a_registry_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "config", "user.name", "Test")
            git(root, "config", "user.email", "test@example.com")
            manifest = root / "go.mod"
            manifest.write_text("module example.com/app\n", encoding="utf-8")
            git(root, "add", "go.mod")
            git(root, "commit", "-m", "base")

            manifest.write_text(
                "module example.com/app\n\n"
                "require github.com/spf13/cobra v1.8.1\n",
                encoding="utf-8",
            )
            report = ProjectScanner(
                root,
                resolver=PackageRepositoryResolver(offline=True),
            ).scan()

            self.assertEqual(
                [candidate.repository for candidate in report.candidates],
                ["spf13/cobra"],
            )

    def test_scans_new_git_submodule_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".gitmodules").write_text(
                '[submodule "vendor/demo"]\n'
                "  path = vendor/demo\n"
                "  url = https://github.com/acme/demo.git\n",
                encoding="utf-8",
            )
            report = ProjectScanner(
                root,
                resolver=PackageRepositoryResolver(offline=True),
            ).scan()

            self.assertEqual(
                [candidate.repository for candidate in report.candidates],
                ["acme/demo"],
            )


if __name__ == "__main__":
    unittest.main()
