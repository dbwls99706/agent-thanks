import shlex
import unittest

from agent_thanks.session import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    analyze_command_line,
    scan_session_evidence,
)


URL = "https://github.com/used/project"


class ShellBoundaryTests(unittest.TestCase):
    def evidence(self, command: str, *, outcome: str = OUTCOME_OK):
        return scan_session_evidence(
            command, "command", outcome=outcome,
            single_statement=True, provenance=False,
        )

    def test_quoted_or_escaped_operators_never_invent_another_command(self) -> None:
        for operator in ("&&", ";", "||", "|", "&"):
            for quoted in (shlex.quote(operator), '"' + operator + '"', "".join("\\" + c for c in operator)):
                command = f"echo {quoted} git clone {URL}"
                with self.subTest(command=command):
                    evidence = self.evidence(command)
                    self.assertTrue(evidence)
                    self.assertFalse(any(item.meaningful for _, item in evidence))
                    self.assertEqual(analyze_command_line(command).repositories, [])

    def test_shlex_join_retains_argument_boundaries(self) -> None:
        for argv in (
            ["echo", "&&", "git", "clone", URL],
            ["echo", "a b", "||", "git", "clone", URL],
            ["printf", "%s", "&&", "git", "clone", URL],
            ["echo", "&& git clone " + URL],
        ):
            with self.subTest(argv=argv):
                self.assertFalse(any(item.meaningful for _, item in self.evidence(shlex.join(argv))))

    def test_actual_successful_and_chain_and_quoted_operands_stay_verified(self) -> None:
        for command in (
            f"cd work && git clone {URL}",
            f"cd 'folder with spaces' && git clone '{URL}' 'checkout folder'",
            f'git clone "{URL}" "&&"',
            f"git clone '{URL}' ';'",
            f"echo 'ready && waiting' && git clone {URL}",
            f"echo ready\\&waiting && git clone {URL}",
            f"git clone {URL} # reference comment",
        ):
            with self.subTest(command=command):
                evidence = self.evidence(command)
                self.assertEqual([repository for repository, item in evidence if item.meaningful], ["used/project"])

    def test_failure_or_unsafe_actual_control_flow_stays_review_only(self) -> None:
        commands = (
            f"echo ready || git clone {URL}",
            f"echo ready; git clone {URL}",
            f"echo ready | git clone {URL}",
            f"git clone {URL} &",
            f"git clone {URL} && false",
            f"git clone {URL} &&& echo invalid",
            f"git clone {URL};",
            f"git clone {URL} && # no following command",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(any(item.meaningful for _, item in self.evidence(command)))
        self.assertFalse(any(item.meaningful for _, item in self.evidence(
            f"cd work && git clone '{URL}'", outcome=OUTCOME_ERROR
        )))

    def test_active_substitutions_cannot_claim_their_outer_result(self) -> None:
        for command in (
            f"echo `git clone {URL}`",
            f"echo $(git clone {URL})",
            f'echo "$(git clone {URL})"',
        ):
            with self.subTest(command=command):
                self.assertFalse(any(item.meaningful for _, item in self.evidence(command)))
        self.assertTrue(self.evidence(f"echo '$(not evaluated)' && git clone {URL}")[0][1].meaningful)

    def test_url_hash_and_comment_boundaries_remain_literal(self) -> None:
        for command in (f"git clone {URL}#branch", f"git clone '{URL}#branch'", f"git clone {URL} folder#name"):
            with self.subTest(command=command):
                self.assertEqual(analyze_command_line(command).repositories, ["used/project"])
        command = f"echo ignored # && git clone {URL}"
        self.assertFalse(any(item.meaningful for _, item in self.evidence(command)))

    def test_active_redirection_cannot_turn_an_echo_prefix_into_false_use(self) -> None:
        for command in (
            f"echo true > /tmp/git && git clone {URL}",
            f"echo true >>/tmp/git && git clone {URL}",
            f"echo true 1>'/tmp/git' && git clone {URL}",
            f"echo true <>/tmp/git && git clone {URL}",
            f"git clone {URL} >output.log",
            f"git clone {URL} 2>&1",
            f"git clone {URL} &>output.log",
            f"git clone {URL} <input.txt",
            f"git clone {URL} <<END",
        ):
            with self.subTest(command=command):
                self.assertFalse(any(item.meaningful for _, item in self.evidence(command)))

    def test_quoted_and_escaped_redirection_characters_remain_data(self) -> None:
        for command in (
            f"echo '>' /tmp/git && git clone {URL}",
            f"echo '<' input && git clone {URL}",
            f"echo \\> /tmp/git && git clone {URL}",
            f"git clone '{URL}' 'folder>name'",
            f"git clone '{URL}' folder\\<name",
            f"git clone '{URL}?compare=2>1'",
            f"git clone {URL}?compare=2\\>1",
        ):
            with self.subTest(command=command):
                evidence = self.evidence(command)
                self.assertEqual([repository for repository, item in evidence if item.meaningful], ["used/project"])


if __name__ == "__main__":
    unittest.main()
