# Design notes

## Product boundary

`agent-thanks` reports repositories observable during one coding task. It does
not claim to identify repositories in a model's training data or infer invisible
influences.

The current release recognizes two evidence families:

1. A direct dependency added since a Git baseline.
2. A GitHub repository present in an explicitly supplied session log.

Only deterministic, high-confidence evidence of substantive use makes a
candidate eligible for the interactive Star flow. A plain repository reference
remains visible for review but is never eligible for mutation.

## Human confirmation invariant

Automated detection and evidence export are supported. Automated starring is
not.

Every live Star mutation must pass all of these gates:

1. The candidate has high-confidence meaningful-use evidence.
2. The process has an interactive terminal; piped input is rejected.
3. The authenticated GitHub account is displayed.
4. Existing Stars are excluded before prompting, so the same account is not
   asked about the same repository again.
5. The user answers `y` for that exact new repository; the default is No.
6. The user confirms the final selected count.

`--repo` narrows the eligible set but cannot raise a candidate's confidence.
There is no saved consent policy, unattended confirmation flag, or bulk
low-confidence override. Legacy 0.3.x consent configuration is not read.

Unstar follows the same interactive pattern. Before any mutation the client
checks the current Star state. A successful Star batch prints an Undo receipt
containing only Stars created by that invocation. If an API or network failure
occurs after partial progress, the same receipt is printed before a non-zero
exit.

## Rank-neutral operations

The following operations never mutate GitHub:

- `demo`
- `scan`
- `review`
- `export`
- any command using `--dry-run`

They can run non-interactively in CI. `doctor` also makes no mutation, but it
does contact GitHub to identify the active account.

## Export boundary

The JSON report is the complete local audit artifact and can include absolute
project or session paths. Markdown export is intended for human-reviewed
sharing. It:

- includes verified-use candidates by default;
- optionally adds a separate low-confidence reference section;
- never changes confidence or Star eligibility;
- removes absolute directory prefixes from evidence-source labels;
- performs no network or authentication work.

## Trust boundaries

- Session-log contents stay local and are never sent to a model or registry.
- Package names may be sent to PyPI, npm, or crates.io unless `--offline` is
  used.
- GitHub-hosted Go paths, Git submodule URLs, and direct Git URLs resolve
  locally.
- GitHub account lookup, existing-Star lookup, Star, and Unstar use the GitHub
  API or an authenticated GitHub CLI session.
- Credentials are read from `GH_TOKEN`, `GITHUB_TOKEN`, or GitHub CLI and are
  never persisted by `agent-thanks`.
- Raw reports can expose local paths and package names and should not be
  committed blindly.

## Detection roadmap

- Agent adapters that emit a small, stable provenance event format.
- Added imports and lockfile-aware direct/transitive classification.
- `ATTRIBUTION.md` discovery and validation, with `mode: suggest` always routed
  through repository-specific confirmation.
- Mirrors, monorepos, and registry-metadata confidence.
- A local audit ledger with reversible action history.
