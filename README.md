# agent-thanks

[![Tests](https://github.com/dbwls99706/agent-thanks/actions/workflows/tests.yml/badge.svg)](https://github.com/dbwls99706/agent-thanks/actions/workflows/tests.yml)
[![Python 3.10–3.14](https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/dbwls99706/agent-thanks/blob/main/LICENSE)

Give open-source maintainers a visible thank-you when an AI coding agent
meaningfully uses their work.

<p align="center">
  <img src="https://raw.githubusercontent.com/dbwls99706/agent-thanks/main/docs/assets/agent-thanks-banner.svg" alt="AI: done in 12 seconds. Open source: 12 years in the making. Leave a star." width="900">
</p>

`agent-thanks` turns one coding task into a reviewable gratitude loop:

1. Detect GitHub repositories used during the task.
2. Show exactly why each repository was detected.
3. Ask for consent or apply the user's saved auto policy.

It is agent-agnostic, uses deterministic rules, and does not call another AI
model. Search results and briefly viewed repositories are never auto-starred.

> The project is intentionally conservative. Start in `ask` mode and use
> `--dry-run` until the report matches your expectations.

## Install

For an isolated command-line installation, use
[pipx](https://pipx.pypa.io/stable/):

```bash
pipx install git+https://github.com/dbwls99706/agent-thanks.git
```

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/dbwls99706/agent-thanks.git
```

Plain pip also works:

```bash
python -m pip install "agent-thanks @ git+https://github.com/dbwls99706/agent-thanks.git"
```

Python 3.10 through 3.14 is supported on Linux, macOS, and Windows.

## Start in 60 seconds

Authenticate once with the GitHub CLI, or provide a fine-grained user token:

```bash
gh auth login
```

Choose the consent policy, verify the setup, and preview the first run:

```bash
agent-thanks config
agent-thanks doctor --repo .
agent-thanks run --repo . --session path/to/agent-session.log --dry-run
```

Remove `--dry-run` when the evidence looks right:

```bash
agent-thanks run --repo . --session path/to/agent-session.log
```

Typical output:

```text
GitHub account: @octocat
Starred: https://github.com/BehaviorTree/BehaviorTree.CPP
Already starred: https://github.com/ros-navigation/navigation2
Undo this batch: agent-thanks unstar BehaviorTree/BehaviorTree.CPP
```

The account is shown before any mutation. Existing Stars remain untouched, and
the Undo receipt contains only Stars created by that invocation.

<p align="center">
  <img src="https://raw.githubusercontent.com/dbwls99706/agent-thanks/main/docs/assets/terminal-walkthrough.svg" alt="Terminal walkthrough: detect repositories, review evidence, approve a Star, verify the GitHub account, and receive an Undo command." width="900">
</p>

<p align="center"><sub>Detect → review → approve → thank. The illustration follows the real CLI order.</sub></p>

## Consent modes

| Mode | Behaviour |
| --- | --- |
| `ask` | Show every candidate and wait for an explicit `yes` or `no`. This is the safe default. |
| `auto` | Star every candidate with high-confidence evidence of meaningful use. |

Configure the saved mode at any time:

```bash
agent-thanks config --mode ask
agent-thanks config --mode auto
agent-thanks config --show
```

Override it for one invocation without changing the saved choice:

```bash
agent-thanks run --mode ask --dry-run
agent-thanks run --mode auto --dry-run
```

Low-confidence references stay unstarred in `auto` mode. Including them requires
the explicit bulk command `agent-thanks star --all --yes`.

## What gets detected

| Source | Coverage | Evidence level |
| --- | --- | --- |
| `requirements*.txt`, `pyproject.toml` | Python direct dependencies | High |
| `package.json` | npm direct dependencies | High |
| `Cargo.toml` | Rust direct dependencies | High |
| `go.mod` | Direct Go modules; GitHub paths map without a registry lookup | High |
| `.gitmodules` | GitHub submodules | High |
| Agent transcript | Clone, submodule, Git install, or explicit code-provenance lines | High |
| Plain GitHub URL in a transcript | Any public GitHub repository | Low; manual review |

Package names from PyPI, npm, and crates.io are mapped using public registry
metadata. Use `--offline` to disable these lookups. Go modules and Git submodules
whose source is already a GitHub URL need no registry request.

High-confidence transcript evidence includes:

- `git clone` and `gh repo clone`
- `git submodule add`
- Git-based `pip`, `uv`, npm, pnpm, Yarn, Cargo, and Go commands
- Explicit `copied from`, `adapted from`, or `used code from` provenance

Repository starring itself is language-independent. Any valid public GitHub
`owner/repository` detected in the report can be reviewed and starred.

## Common workflows

### Uncommitted agent changes

`HEAD` represents the project before the current working-tree changes:

```bash
agent-thanks run --repo . --base HEAD --session agent-session.log --dry-run
```

### Work already committed

Point `--base` to the revision before the agent's work:

```bash
agent-thanks run --repo . --base HEAD~1 --session agent-session.log --dry-run
```

### Dependency changes only

The transcript is optional. This scans supported manifests only:

```bash
agent-thanks run --repo . --dry-run
```

### Pipe a transcript without saving it

```bash
your-agent-log-command | agent-thanks scan --repo . --session -
```

More examples are in the [usage recipes](https://github.com/dbwls99706/agent-thanks/blob/main/docs/recipes.md).

## Read-only review workflow

Scanning and starring can be separated completely:

```bash
agent-thanks scan --repo . --session agent-session.log
agent-thanks review .agent-thanks-report.json
agent-thanks star .agent-thanks-report.json
```

`scan`, `review`, and every `--dry-run` invocation are read-only and do not even
construct an authenticated GitHub client.

To select exact candidates from a report:

```bash
agent-thanks star --repo owner/one --repo owner/two
```

To reverse a Star manually:

```bash
agent-thanks unstar owner/repository
```

## GitHub authentication

The tool uses `GH_TOKEN` first, then `GITHUB_TOKEN`, then an existing GitHub CLI
login. It never writes a credential.

A fine-grained user token needs:

- `Starring: write`
- `Metadata: read`

The Star endpoint always acts on the authenticated user. Run `agent-thanks
doctor` to see that account before doing any work.

Missing authentication, expired credentials, insufficient permission, rate
limits, and rejected requests return a non-zero exit code. A failed request is
never printed as a successful Star. If a batch stops after partial progress,
the CLI prints an Undo command for the completed subset.

See the [troubleshooting guide](https://github.com/dbwls99706/agent-thanks/blob/main/docs/troubleshooting.md) for common 401, 403, 404,
empty-report, and dependency-resolution cases.

## Privacy and safety

- Session logs remain on the local machine.
- Only package names are sent to public package registries.
- Reports may contain local paths and dependency names; do not commit them
  blindly.
- `ask` defaults every candidate to No and requires a final batch approval.
- `auto` is opt-in and applies only to verified meaningful-use evidence.
- Existing Stars are detected and excluded from Undo receipts.
- Requests are serialized rather than sent as a high-speed bulk burst.

The tool cannot identify repositories contained in a model's training data. It
reports only evidence observable in the current task. Its consent and
meaningful-use rules are compatible with the emerging
[ATTRIBUTION.md](https://github.com/attributionmd/attribution.md) convention.

## Command overview

```text
agent-thanks doctor   Verify project, configuration, and GitHub account
agent-thanks config   Choose ask or auto
agent-thanks run      Scan and apply the saved policy
agent-thanks scan     Create a read-only evidence report
agent-thanks review   Inspect report evidence
agent-thanks star     Apply a policy to a saved report
agent-thanks unstar   Revoke exact Stars
```

Run `agent-thanks COMMAND --help` for every option.

## Development

The runtime uses only the Python standard library on Python 3.11 and newer.
Python 3.10 adds the small `tomli` compatibility dependency.

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

CI tests Python 3.10 through 3.14 and runs additional Windows and macOS smoke
jobs. Product boundaries and trust assumptions are documented in
[design notes](https://github.com/dbwls99706/agent-thanks/blob/main/docs/design.md).

Contributions and bug reports are welcome. See
[contribution guide](https://github.com/dbwls99706/agent-thanks/blob/main/CONTRIBUTING.md),
[security policy](https://github.com/dbwls99706/agent-thanks/blob/main/SECURITY.md),
and [code of conduct](https://github.com/dbwls99706/agent-thanks/blob/main/CODE_OF_CONDUCT.md).

## License

MIT
