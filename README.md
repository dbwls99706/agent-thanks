# agent-thanks

[![Tests](https://github.com/dbwls99706/agent-thanks/actions/workflows/tests.yml/badge.svg)](https://github.com/dbwls99706/agent-thanks/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Give open-source maintainers a visible thank-you when an AI coding agent
meaningfully uses their work.

`agent-thanks` detects repositories used in one coding task, shows the evidence,
and applies the consent policy chosen by the user. It is safe-by-default,
agent-agnostic, and does not require another AI model.

<p align="center">
  <img src="docs/assets/agent-thanks-banner.svg" alt="AI: done in 12 seconds. Open source: 12 years in the making. Leave a star." width="900">
</p>

> Alpha software. Detection is intentionally conservative; keep `ask` mode
> enabled if you want to approve every repository yourself.

## The two consent modes

The first setup asks the user to choose one policy:

| Mode | Behaviour |
| --- | --- |
| `ask` | Show every detected repository and wait for an explicit `yes` or `no`. This is the default. |
| `auto` | Automatically star every repository with high-confidence evidence of meaningful use. |

`auto` never stars a repository that merely appeared in a search result, was
briefly viewed, or otherwise has only low-confidence evidence. Those items stay
in the report for manual review.

```text
How should agent-thanks handle repositories with verified, meaningful use?
  1. Ask every time — show each repository and wait for yes/no (recommended)
  2. Auto star all — star every verified repository without another prompt

Viewed-only and low-confidence repositories are never auto-starred.
Choose [1/2] (default: 1):
```

The choice is saved, can be changed at any time, and can be overridden for a
single run.

## Quick start

Python 3.10 or newer is required.

```bash
git clone https://github.com/dbwls99706/agent-thanks.git
cd agent-thanks
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

# Choose ask or auto once
agent-thanks config

# Scan the task and immediately apply the saved policy
agent-thanks run --repo . --session path/to/agent-session.log
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

## Choose or change the policy

Interactive setup:

```bash
agent-thanks config
```

Set a policy directly:

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

By default, configuration is stored at:

- Linux/macOS: `~/.config/agent-thanks/config.json`
- Windows: `%APPDATA%\agent-thanks\config.json`

`XDG_CONFIG_HOME` is respected, and `AGENT_THANKS_CONFIG` can point to a custom
file. No GitHub token is stored in this file.

## How detection works

The current release collects two evidence families:

1. Direct dependencies added since a Git base revision.
2. GitHub repositories present in an explicitly supplied agent transcript.

High-confidence evidence includes:

- A new direct dependency in a supported manifest
- `git clone` or `gh repo clone`
- `git submodule add`
- Direct Git-based package installation
- Explicit `copied from`, `adapted from`, or `used code from` provenance

A plain GitHub URL is recorded as a low-confidence reference. It is shown in
`ask` mode but defaults to `No`; it is skipped entirely by `auto` mode.

Supported manifests:

- Python: `requirements*.txt`, `pyproject.toml`
- Node.js: `package.json`
- Rust: `Cargo.toml`

Package names are mapped to their repositories using public PyPI, npm, and
crates.io metadata. Use `--offline` to disable those lookups.

## One-command and separated workflows

### One command

`run` scans, saves the evidence report, and applies the chosen policy:

```bash
agent-thanks run \
  --repo . \
  --base HEAD \
  --session agent-session.log
```

If the agent's work is already committed, point `--base` to the revision from
before the work began:

```bash
agent-thanks run --base HEAD~1
```

### Read-only scan first

`scan` and `review` never modify the GitHub account:

```bash
agent-thanks scan --repo . --session agent-session.log
agent-thanks review .agent-thanks-report.json
agent-thanks star .agent-thanks-report.json
```

`star` applies the saved `ask` or `auto` policy to the report.

### Explicit selections

```bash
# Star one exact candidate from the report
agent-thanks star --repo owner/repository

# Non-interactively star all verified candidates
agent-thanks star --yes

# Explicitly include low-confidence references as well
agent-thanks star --all --yes

# Preview any operation without changing GitHub
agent-thanks star --yes --dry-run

# Revoke a star
agent-thanks unstar owner/repository
```

`--all --yes` is intentionally required to include low-confidence references.
Setting `auto` alone is not enough.

## Example report

```text
[verified | high] https://github.com/BehaviorTree/BehaviorTree.CPP
  - Session shows a substantive repository-use command (session.log:12)

[review | low] https://github.com/example/reference-only
  - Repository was referenced in the session; verify actual reuse (session.log:4)
```

## GitHub authentication

The tool first uses `GH_TOKEN` or `GITHUB_TOKEN` when present. Otherwise it uses
an existing GitHub CLI login:

```bash
gh auth login
```

A fine-grained user token needs:

- `Starring: write`
- `Metadata: read`

Credentials are never written by `agent-thanks`. Star requests are serialized,
and `unstar` provides an explicit reversal path. Missing authentication,
expired credentials, insufficient permissions, and rejected API requests stop
the command with a non-zero exit code; a failed request is never reported as a
successful star.

## Privacy and safety

- Session logs remain on the local machine.
- Only package names are sent to public package registries.
- The evidence report may contain local paths and dependency names; do not
  commit it blindly.
- `scan`, `review`, and `--dry-run` are read-only.
- `ask` defaults every repository to `No` and requires a final batch approval.
- `auto` is an explicit opt-in and applies only to verified meaningful use.
- A search result, brief inspection, or model-training guess is not meaningful
  reuse.

The project cannot identify repositories contained in a model's training data.
It reports only evidence observable in the current coding task.

These principles are compatible with the consent and meaningful-reuse ideas in
the emerging [ATTRIBUTION.md](https://github.com/attributionmd/attribution.md)
convention, though full protocol support remains on the roadmap.

## CLI overview

```text
agent-thanks config   Choose ask or auto
agent-thanks run      Scan and apply the saved policy
agent-thanks scan     Create a read-only report
agent-thanks review   Inspect report evidence
agent-thanks star     Apply a policy to a saved report
agent-thanks unstar   Revoke stars
```

Run `agent-thanks COMMAND --help` for all options.

## Development

The runtime uses only the Python standard library on Python 3.11+. Python 3.10
adds the small `tomli` compatibility dependency.

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

CI tests Python 3.10 through 3.13. See [docs/design.md](docs/design.md) for
product boundaries, trust assumptions, and detection rules. Contributions are
described in [CONTRIBUTING.md](CONTRIBUTING.md).

Bug reports and feature proposals are welcome through GitHub Issues. Please
follow [SECURITY.md](SECURITY.md) for vulnerability reports and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) in all project spaces.

## License

MIT
