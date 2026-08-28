# Usage recipes

Start with `--dry-run`. Run a live `star` command only after reviewing the
evidence in an interactive terminal.

## Preview without credentials

```bash
agent-thanks demo
```

The built-in demo performs no network requests and changes nothing.

## Verify the environment

```bash
agent-thanks doctor --repo .
```

`doctor` checks Python, Git, the project directory, the per-repository approval
policy, and the GitHub login that would own a Star. It does not mutate GitHub.

## Scan uncommitted work

Use the current `HEAD` as the state before working-tree changes:

```bash
agent-thanks run \
  --repo . \
  --base HEAD \
  --session session.log \
  --dry-run
```

Staged, unstaged, and untracked supported manifests are included.

## Scan one committed task

If the task is the most recent commit:

```bash
agent-thanks run \
  --repo . \
  --base HEAD~1 \
  --session session.log \
  --dry-run
```

For a longer range, pass the commit immediately before the task as `--base`.

## Scan dependencies without a session log

```bash
agent-thanks scan --repo .
agent-thanks review .agent-thanks-report.json
```

This reports newly introduced direct dependencies in supported manifests. It
does not guess which repositories may have been consulted without a recorded
signal.

## Scan multiple session logs

```bash
agent-thanks scan \
  --repo . \
  --session planning.log \
  --session implementation.log \
  --session review.log
```

Evidence for the same repository is deduplicated into one candidate.

## Read a session log from standard input

```bash
your-log-command | agent-thanks scan --repo . --session -
```

Standard input stays local. Use a separate interactive `star` command after
reviewing the saved report.

## Work without registry access

```bash
agent-thanks run --repo . --session session.log --offline --dry-run
```

Direct GitHub URLs, Git submodules, and GitHub-hosted Go modules still resolve.
Package names requiring PyPI, npm, or crates.io metadata remain unresolved.

## Review now, decide later

```bash
agent-thanks scan --repo . --session session.log
agent-thanks review .agent-thanks-report.json
agent-thanks export .agent-thanks-report.json --output OPEN_SOURCE_USE.md
agent-thanks star .agent-thanks-report.json
```

The first three commands are rank-neutral. The final command requires a terminal
and asks separately for each eligible repository.

## Export evidence in CI

```bash
agent-thanks scan \
  --repo . \
  --base HEAD~1 \
  --offline \
  --output .agent-thanks-report.json

agent-thanks export \
  .agent-thanks-report.json \
  --output OPEN_SOURCE_USE.md
```

Add `--include-low-confidence` only when the review-only reference section is
useful. Export makes no network request, removes absolute directory prefixes,
and never changes a Star.

## Approve exact verified candidates

```bash
agent-thanks star \
  .agent-thanks-report.json \
  --repo owner/first \
  --repo owner/second
```

Every requested repository must already be verified in the report. Each receives
its own default-No prompt followed by a final count confirmation.

## Undo a completed Star batch

After adding Stars, the CLI prints only the repositories newly changed by that
invocation:

```text
Undo this batch: agent-thanks unstar owner/one owner/two
```

Run the command in a terminal. Each Unstar also requires explicit approval.
Repositories already starred before the batch are excluded from the receipt.

## Migrate a 0.3.x automation

Replace any old command that changed Stars non-interactively with evidence-only
steps:

```bash
agent-thanks scan --repo . --output .agent-thanks-report.json
agent-thanks export .agent-thanks-report.json --output OPEN_SOURCE_USE.md
agent-thanks star .agent-thanks-report.json --dry-run
```

Legacy consent configuration is ignored. Run `agent-thanks star` interactively
when a person is ready to decide repository by repository.
