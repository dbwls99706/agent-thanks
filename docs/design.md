# Design notes

## Product boundary

`agent-thanks` reports repositories that can be observed during one coding task. It
does not claim to identify repositories contained in a model's training data.

The current release recognizes two evidence families:

1. A direct dependency added since a Git base revision.
2. A GitHub repository present in an explicitly supplied agent transcript.

A repository reference is recommended automatically only when deterministic
evidence indicates substantive use. Viewing a page is insufficient.

## Consent model

The user chooses one persistent policy during setup:

- `ask` presents every candidate with a default-No yes/no prompt, followed by
  final batch confirmation.
- `auto` selects every high-confidence meaningful-use candidate without another
  prompt. It never selects low-confidence or viewed-only candidates.

`run --mode ...` and `star --mode ...` provide one-time overrides without
changing the stored policy. Non-interactive inclusion of low-confidence
references requires the deliberately redundant `--all --yes` combination.

Scanning and review remain read-only regardless of the stored mode. `unstar`
provides a reversal path. Before mutating, the CLI identifies the authenticated
account and checks whether each repository is already starred. A successful
batch prints an Undo receipt containing only Stars created by that invocation.

## Trust boundaries

- Session logs stay local and are never sent to an AI service.
- Package names may be sent to their public registry unless `--offline` is used.
- GitHub-hosted Go module paths and Git submodule URLs resolve locally without a
  registry lookup.
- Credentials are read from `GH_TOKEN`, `GITHUB_TOKEN`, or an authenticated
  GitHub CLI session and are never persisted by `agent-thanks`.
- The consent policy contains no credential and is saved atomically with owner-
  only permissions on POSIX systems.
- Report files can reveal local paths and package names and should not be
  committed blindly.
- `doctor`, `scan`, `review`, and `--dry-run` never perform a Star mutation.

## Detection roadmap

- Agent adapters that emit a small, stable provenance event format.
- Added imports and lockfile-aware direct/transitive classification.
- `ATTRIBUTION.md` discovery and validation.
- Mirrors, monorepos, and registry metadata confidence.
- A local audit ledger with reversible action history.
