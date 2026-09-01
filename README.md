# agent-thanks

[![Tests](https://github.com/dbwls99706/agent-thanks/actions/workflows/tests.yml/badge.svg)](https://github.com/dbwls99706/agent-thanks/actions/workflows/tests.yml)
[![Python 3.10–3.14](https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/dbwls99706/agent-thanks/blob/main/LICENSE)
<a href="https://smollaunch.com" target="_blank" rel="noopener">
  <img
    src="https://smollaunch.com/badges/featured.svg"
    alt="agent-thanks - Featured on Smol Launch"
    loading="lazy"
    width="150"
  >
</a>

Give open-source maintainers a visible thank-you when a coding agent
meaningfully uses their work.

<p align="center">
  <img src="https://raw.githubusercontent.com/dbwls99706/agent-thanks/main/docs/assets/agent-thanks-banner.svg" alt="AI: done in 12 seconds. Open source: 12 years in the making. Leave a star." width="900">
</p>

`agent-thanks` reconstructs the open-source footprint of one coding task:

1. Detect repositories from newly added dependencies and an optional session log.
2. Show the evidence behind every match.
3. Export a shareable Markdown record or approve eligible Stars one by one.

Detection uses deterministic rules and never calls another model. A plain URL is
treated as a reference, not proof of use. Every live Star requires an explicit
`y/N` decision in an interactive terminal; there is no unattended or bulk-Star
mode.

## Try it first

Install the CLI and run its built-in, read-only demo:

```bash
pipx install git+https://github.com/dbwls99706/agent-thanks.git
agent-thanks demo
```

The install requires network access. The demo itself makes no network requests,
reads no credentials, writes no files, and changes no Stars. It includes both
verified use and a low-confidence reference so the boundary is visible.

## Start in 60 seconds

Preview one task without authenticating to GitHub:

```bash
agent-thanks run \
  --repo . \
  --base HEAD \
  --session path/to/agent-session.log \
  --dry-run
```

This writes `.agent-thanks-report.json`, explains every candidate, and prints
which verified repositories would be offered for approval. Review or export the
same report at any time:

```bash
agent-thanks review .agent-thanks-report.json
agent-thanks export .agent-thanks-report.json --output OPEN_SOURCE_USE.md
```

When the evidence looks right, authenticate and start the interactive flow:

```bash
gh auth login
agent-thanks doctor --repo .
agent-thanks star .agent-thanks-report.json
```

Typical interaction:

```text
Review only: 1 candidate(s) lack high-confidence meaningful-use evidence and cannot be starred.
GitHub account: @octocat
Each new Star requires an explicit yes. The default is No.

[verified | high] https://github.com/BehaviorTree/BehaviorTree.CPP
  - Session shows a substantive repository-use command (session.log:8)
Star this repository? [y/N/q] y

Proceed with 1 star(s)? [y/N] y
Starred: https://github.com/BehaviorTree/BehaviorTree.CPP
Undo this batch: agent-thanks unstar BehaviorTree/BehaviorTree.CPP
```

The authenticated account is shown before any decision. Existing Stars are
checked and reported without another mutation. Every repository that needs a
new Star requires its own `y`; there is no approve-all choice. The Undo receipt
contains only Stars created by that invocation.

<p align="center">
  <img src="https://raw.githubusercontent.com/dbwls99706/agent-thanks/main/docs/assets/terminal-walkthrough.svg" alt="Terminal walkthrough: detect repositories, review evidence, approve a Star, verify the GitHub account, and receive an Undo command." width="900">
</p>

<p align="center"><sub>Detect → inspect → approve → thank. The illustration follows the real CLI flow.</sub></p>

## The safety boundary

| Result | Meaning | Star behavior |
| --- | --- | --- |
| Verified use | High-confidence evidence of a newly added dependency, clone, Git install, submodule, or explicit provenance | Eligible for a default-No `y/N` prompt |
| Reference to review | A GitHub URL appeared, but meaningful use is not established | Visible in reports; never eligible for Star |
| Unresolved dependency | A package could not be mapped to a GitHub source | Reported without guessing or mutation |

The CLI enforces this boundary in code:

- A live Star requires a real interactive terminal.
- Piped and unattended confirmations are rejected.
- Every eligible repository gets its own default-No prompt and a final summary.
- `--repo` can narrow the eligible set but cannot elevate a low-confidence item.
- `demo`, `scan`, `review`, `export`, and every `--dry-run` are rank-neutral.
- Star and Unstar failures return non-zero and never print false success.

Automation is intentionally limited to detection and evidence export. This keeps
CI workflows useful without turning a human signal into an unattended action.

## What gets detected

| Source | Coverage | Evidence level |
| --- | --- | --- |
| `requirements*.txt`, `pyproject.toml` | Python direct dependencies | High |
| `package.json` | npm direct dependencies | High |
| `Cargo.toml` | Rust direct dependencies | High |
| `go.mod` | Direct Go modules; GitHub paths map locally | High |
| `.gitmodules` | GitHub submodules | High |
| Session log | Clone, submodule, Git install, or explicit code-provenance lines | High |
| Plain GitHub URL in a session log | Any public GitHub repository | Low; review only |

High-confidence session evidence includes:

- `git clone` and `gh repo clone`
- `git submodule add`
- Git-based `pip`, `uv`, npm, pnpm, Yarn, Cargo, and Go commands
- Provenance lines that start with `copied from`, `adapted from`, or
  `used code from` and name the repository as their direct target

Package names from PyPI, npm, and crates.io are mapped through public registry
metadata. `--offline` disables those lookups. GitHub-hosted Go modules, Git
submodules, and direct Git URLs can resolve locally.

Repository starring is language-independent: any valid public GitHub
`owner/repository` can appear in a report when supported evidence identifies it.
The dependency-diff scanners currently cover Python, npm, Cargo, and Go; session
evidence covers public GitHub repositories from any ecosystem.

## Why task-level evidence?

Dependency-tree tools answer “what does this project depend on?” `agent-thanks`
asks a narrower question: **what was newly and observably used during this one
task?**

It compares the current work with a Git baseline, accepts an explicitly supplied
session log, explains every match, and separates meaningful use from a URL that
was merely present. It does not claim to identify model-training sources or
unobservable influences.

## Common workflows

### Uncommitted work

Use `HEAD` as the project state before current working-tree changes:

```bash
agent-thanks run --repo . --base HEAD --session session.log --dry-run
```

### Work already committed

Point `--base` to the revision immediately before the task:

```bash
agent-thanks run --repo . --base HEAD~1 --session session.log --dry-run
```

### Dependency changes only

The session log is optional:

```bash
agent-thanks scan --repo . --base HEAD
```

### Read a session log from standard input

```bash
your-log-command | agent-thanks scan --repo . --session -
```

Use `scan` for piped input. A later `star` command must run in an interactive
terminal.

### Work without registry lookups

```bash
agent-thanks scan --repo . --session session.log --offline
```

Packages that need registry metadata remain in the unresolved section. They are
never guessed.

### Approve exact verified candidates

```bash
agent-thanks star .agent-thanks-report.json \
  --repo owner/first \
  --repo owner/second
```

Every requested repository must exist in the report and already have
high-confidence meaningful-use evidence. Each still receives its own prompt.

More examples are in the [usage recipes](docs/recipes.md).

## Markdown evidence export

Export verified use to a file suitable for review before adding it to a PR or
release note:

```bash
agent-thanks export .agent-thanks-report.json --output OPEN_SOURCE_USE.md
```

Include a clearly separated section of low-confidence references when useful:

```bash
agent-thanks export .agent-thanks-report.json \
  --include-low-confidence \
  --output OPEN_SOURCE_USE.md
```

Export is deterministic, performs no network or account action, and removes
absolute local directory prefixes from evidence sources. The JSON report remains
the complete local record and may contain absolute paths.

## GitHub authentication

The tool reads credentials in this order:

1. `GH_TOKEN`
2. `GITHUB_TOKEN`
3. an authenticated GitHub CLI session

It never stores a token. A fine-grained user token needs:

- `Starring: write`
- `Metadata: read`

GitHub's Star endpoint acts on the authenticated user. `agent-thanks doctor`
shows that account before the interactive flow. Missing authentication, expired
credentials, insufficient permission, unavailable repositories, rate limits,
and rejected requests return a non-zero exit code.

Requests are serialized. If a multi-repository operation stops after partial
progress, the CLI prints an Undo command for the completed subset.

See [troubleshooting](docs/troubleshooting.md) for 401, 403, 404, empty-report,
and dependency-resolution cases.

## Privacy and network behavior

| Operation | Network behavior |
| --- | --- |
| `demo` | None |
| Session-log scan | Contents stay local |
| Package resolution | Sends the package name to PyPI, npm, or crates.io unless `--offline` is used |
| `review` / `export` | None |
| `doctor` | Checks the authenticated GitHub account |
| Live Star / Unstar | Checks account and existing-Star state, then uses the GitHub API for approved mutations |

Reports can contain project paths, session-log paths, and dependency names. The
default report name is ignored by this repository, but users should avoid
committing raw reports without review. Markdown export strips absolute directory
prefixes but should still be reviewed before publication.

## `ATTRIBUTION.md`

Discovery of the draft `ATTRIBUTION.md` v0.1 protocol is on the roadmap; v0.4.0
does not parse that file. Its `mode: suggest` consent boundary already matches
this release's behavior: every live Star is presented as a repository-specific
human decision.

## Migrating from 0.3.x

v0.4.0 removes persistent consent modes and every non-interactive Star path:

- `agent-thanks config` was removed.
- `auto` and `--mode` were removed.
- `star --yes` and `star --all --yes` were removed.
- Low-confidence references can no longer be starred through this CLI.

Existing 0.3.x configuration files are ignored; no cleanup is required. Replace
automation that previously changed Stars with rank-neutral commands such as:

```bash
agent-thanks scan --repo . --output .agent-thanks-report.json
agent-thanks export .agent-thanks-report.json --output OPEN_SOURCE_USE.md
agent-thanks star .agent-thanks-report.json --dry-run
```

Run `agent-thanks star` later in a terminal for per-repository approval.

## Command overview

```text
agent-thanks demo     Preview the flow without credentials or network access
agent-thanks doctor   Verify the project and authenticated GitHub account
agent-thanks run      Scan and enter the interactive approval flow
agent-thanks scan     Create a read-only JSON evidence report
agent-thanks review   Inspect report evidence in the terminal
agent-thanks export   Render a shareable Markdown evidence list
agent-thanks star     Approve eligible Stars one repository at a time
agent-thanks unstar   Revoke exact Stars with interactive confirmation
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

CI tests Python 3.10 through 3.14 and runs Windows, macOS, and built-wheel smoke
jobs. Product boundaries and trust assumptions are documented in the
[design notes](docs/design.md).

Contributions and bug reports are welcome. See the [contribution guide](CONTRIBUTING.md),
[security policy](SECURITY.md), and [code of conduct](CODE_OF_CONDUCT.md).

## License

MIT
