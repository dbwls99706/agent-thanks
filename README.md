# agent-thanks

[![Tests](https://github.com/dbwls99706/agent-thanks/actions/workflows/tests.yml/badge.svg)](https://github.com/dbwls99706/agent-thanks/actions/workflows/tests.yml)
[![Python 3.10–3.14](https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
<a href="https://smollaunch.com" target="_blank" rel="noopener"><img src="https://smollaunch.com/badges/featured.svg" alt="agent-thanks - Featured on Smol Launch" loading="lazy" width="150" height="36"></a>

**Know exactly which open-source repositories your coding agent actually used.**

`agent-thanks` reconstructs task-level open-source provenance from dependency changes and coding-agent activity. It produces a reviewable evidence report first. If you want to thank verified repositories with a GitHub Star, that is a separate, explicit human decision.

<p align="center">
  <img src="https://raw.githubusercontent.com/dbwls99706/agent-thanks/main/docs/assets/agent-thanks-banner.svg" alt="AI: done in 12 seconds. Open source: 12 years in the making. Leave a star." width="900">
</p>

## The idea in one screen

A dependency tree answers **what does this project depend on?**

`agent-thanks` asks a narrower question:

> **What open-source work became newly and observably relevant during this coding task, and what evidence supports that claim?**

It combines:

- direct dependency changes relative to a Git baseline,
- repository-use commands recorded by Claude Code or Codex,
- explicit provenance statements such as `Adapted from ...`,
- conservative handling of references that do not prove use.

The result is deterministic. No model is called to decide whether a repository was used.

```text
coding task
   │
   ├─ manifest diff ───────────────┐
   ├─ agent transcript / hook log ├─> evidence report
   └─ provenance statements ──────┘        │
                                           ├─ export Markdown
                                           └─ optional human-approved Stars
```

## Try it in 30 seconds

Install from GitHub and run the built-in read-only demo:

```bash
pipx install git+https://github.com/dbwls99706/agent-thanks.git
agent-thanks demo
```

Without `pipx`:

```bash
python -m pip install "https://github.com/dbwls99706/agent-thanks/archive/refs/heads/main.zip"
agent-thanks demo
```

The demo makes no network requests, reads no credentials, writes no files, and changes no Stars.

Example output:

```text
[verified | high] https://github.com/BehaviorTree/BehaviorTree.CPP
  - Session ran a repository-use command that completed successfully

[review | low] https://github.com/example/reference-only
  - Repository was referenced in the session; verify actual reuse

Would star: https://github.com/BehaviorTree/BehaviorTree.CPP
```

That distinction is the product: **evidence before attribution, attribution before action.**

<p align="center">
  <img src="docs/assets/terminal-walkthrough.svg" alt="Terminal walkthrough showing the detect, inspect, approve, and thank flow." width="900">
</p>

## Quick start on a real project

### 1. Scan one coding task

For an uncommitted task, use `HEAD` as the state before the current working-tree changes:

```bash
agent-thanks scan --repo . --base HEAD --from claude-code
```

For Codex:

```bash
agent-thanks scan --repo . --base HEAD --from codex
```

Or provide one or more transcripts directly:

```bash
agent-thanks scan \
  --repo . \
  --base HEAD \
  --session path/to/agent-transcript.jsonl
```

This writes `.agent-thanks-report.json` and explains every candidate.

### 2. Review or export the evidence

```bash
agent-thanks review .agent-thanks-report.json
agent-thanks export .agent-thanks-report.json --output OPEN_SOURCE_USE.md
```

The Markdown export is suitable for a PR description, release note, audit record, or internal review. Absolute local directory prefixes are removed from the exported Markdown.

### 3. Optionally thank verified repositories

```bash
gh auth login
agent-thanks star .agent-thanks-report.json
```

A live Star requires an interactive terminal. Every repository gets its own default-No `y/N` prompt, followed by a final confirmation. There is no unattended or approve-all Star mode.

## What counts as evidence?

| Evidence | Result | Star eligible? |
| --- | --- | --- |
| Newly declared direct dependency | Verified use | Yes |
| Clone, submodule, or Git install command with recorded success | Verified use | Yes |
| Explicit provenance statement such as `Adapted from ...` | Verified use | Yes |
| GitHub URL that merely appeared in a transcript | Reference to review | No |
| Command with missing, conflicting, or failed result | Reference to review | No |
| Package that cannot be mapped to a repository | Unresolved | No |

A successful shell process is not enough if the repository-use command itself is ambiguous. Chains such as `git clone URL || true`, `git clone URL; echo ok`, pipelines, background jobs, path-shadowed executables, and mixed commands remain review-only.

Manifest evidence is independent of session evidence: a newly declared direct dependency counts because the project now declares it, not because an install command is assumed to have succeeded.

## Supported sources

| Source | Coverage | Evidence level |
| --- | --- | --- |
| `requirements*.txt`, `pyproject.toml` | Python direct dependencies | High |
| `package.json` | npm direct dependencies | High |
| `Cargo.toml` | Rust direct dependencies | High |
| `go.mod` | Direct Go modules | High |
| `.gitmodules` | GitHub submodules | High |
| Claude Code transcript / hooks | Commands with recorded results and provenance prose | High when success is explicit |
| Codex transcript / hooks | Commands with recorded results and provenance prose | High when success is explicit |
| Gemini transcript | Repository references and provenance review | Review-only for command success today |
| Plain-text log | Commands without a machine-verifiable result | Review-only unless `--trust-session` is supplied |

Package names from PyPI, npm, and crates.io are mapped through public registry metadata. `--offline` disables those lookups. GitHub-hosted Go modules, submodules, and direct Git URLs can resolve locally.

## Coding-agent integration

`agent-thanks` can be used manually from an agent transcript, or connected through hooks so the evidence report appears automatically after a turn.

### Claude Code plugin

```text
/plugin marketplace add dbwls99706/agent-thanks
/plugin install agent-thanks@agent-thanks
```

The plugin records supported shell events and announces newly verified repository use after a completed turn. `/thanks` shows the current report. It never authenticates to GitHub or creates a Star.

### Codex and Gemini

Codex hooks and Gemini `AfterAgent` integration are supported without granting Star authority to the agent. Exact configuration and agent-specific limitations are documented in [Coding-agent integrations](docs/integrations.md).

## Why this is not an auto-star bot

A GitHub Star is a human signal. Automatically generating it would make the signal less meaningful and create obvious abuse incentives.

`agent-thanks` therefore automates only the part machines are good at:

1. collect observable evidence,
2. classify it conservatively,
3. produce a report,
4. leave the social action to a person.

The authenticated GitHub account is shown before any mutation. Existing Stars are detected. Partial failures print an undo command for Stars created by that invocation.

## Privacy and network behavior

| Operation | Network behavior |
| --- | --- |
| `demo` | None |
| Session-log scan | Transcript contents stay local |
| Package resolution | Sends package names to PyPI, npm, or crates.io unless `--offline` is used |
| `review` / `export` | None |
| `doctor` | Checks the authenticated GitHub account |
| Live Star / Unstar | Uses the GitHub API only for repositories you explicitly approve |

Hook logs live under `.agent-thanks/` and are ignored by their own `.gitignore`. On POSIX, state directories and files are tightened to owner-only permissions. Symbolic links are refused in the private state path. Logs may contain raw shell commands, including secrets typed into commands, so they are pruned after 30 days and can be deleted at any time.

## What agent-thanks does not claim

`agent-thanks` does **not** attempt to identify:

- model-training sources,
- invisible influence from a model's weights,
- every transitive dependency,
- whether code is legally derivative,
- authorship percentages,
- semantic similarity inferred by another model.

It reports only evidence that can be observed from the project and the supplied agent activity.

## Common workflows

### Work already committed

Point `--base` to the revision immediately before the task:

```bash
agent-thanks scan --repo . --base HEAD~1 --from claude-code
```

### Dependency changes only

```bash
agent-thanks scan --repo . --base HEAD
```

### Fully offline scan

```bash
agent-thanks scan --repo . --session transcript.jsonl --offline
```

Packages that require registry metadata remain unresolved instead of being guessed.

### Include review-only references in the Markdown export

```bash
agent-thanks export .agent-thanks-report.json \
  --include-low-confidence \
  --output OPEN_SOURCE_USE.md
```

## Documentation

- [Coding-agent integrations](docs/integrations.md)
- [Usage recipes](docs/recipes.md)
- [Design and trust model](docs/design.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md)

## Design principles

**Evidence first.** A plain URL is not proof of use.

**Fail closed.** Missing or contradictory success information does not become verified evidence.

**Local first.** Agent transcripts are parsed locally; no model API is used.

**Human social signals stay human.** Hooks and CI can detect and export, but cannot create Stars.

**Explain every result.** Reports contain the evidence and confidence behind each candidate.

## Status

`agent-thanks` is an alpha project. The evidence model and safety boundary are intentionally conservative while real-world agent transcript formats continue to change.

The project currently supports task-level evidence from Python, npm, Cargo, Go, Git submodules, Git-based install/clone activity, and explicit provenance statements. Future work includes broader ecosystem coverage and interoperable attribution metadata.

## Contributing

Bug reports, transcript fixtures, new ecosystem resolvers, and adversarial cases are especially useful. See [CONTRIBUTING.md](CONTRIBUTING.md).

If you find a case where `agent-thanks` promotes uncertain evidence to verified use, please treat it as a correctness bug.

## License

MIT
