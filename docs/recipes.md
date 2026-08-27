# Usage recipes

This page collects repeatable `agent-thanks` workflows. Start with `--dry-run`
and remove it only after reviewing the evidence.

## Verify the environment

```bash
agent-thanks doctor --repo .
```

`doctor` checks Python, Git, the project directory, the saved consent mode, and
the GitHub login that would own each Star. It performs no Star mutation.

## Scan uncommitted work

Use the current `HEAD` as the state before the working-tree changes:

```bash
agent-thanks run \
  --repo . \
  --base HEAD \
  --session agent-session.log \
  --dry-run
```

Both staged and unstaged manifest changes are included. Untracked supported
manifests are treated as new.

## Scan one committed task

If the task is the most recent commit:

```bash
agent-thanks run \
  --repo . \
  --base HEAD~1 \
  --session agent-session.log \
  --dry-run
```

For a longer range, use the commit immediately before the work as `--base`.

## Scan dependencies without a transcript

```bash
agent-thanks scan --repo .
agent-thanks review .agent-thanks-report.json
```

This detects newly introduced direct dependencies in supported manifests. It
does not guess which repositories were only consulted during the task.

## Scan multiple transcript files

```bash
agent-thanks scan \
  --repo . \
  --session planning.log \
  --session implementation.log \
  --session review.log
```

Evidence for the same repository is deduplicated into one candidate.

## Read a transcript from standard input

```bash
your-agent-log-command | agent-thanks scan --repo . --session -
```

The generated report stays local. Standard input is not uploaded by the tool.

## Work without registry access

```bash
agent-thanks run --repo . --session agent-session.log --offline --dry-run
```

Direct GitHub URLs, Git submodules, and GitHub-hosted Go module paths still map
offline. Package names that require PyPI, npm, or crates.io metadata remain in
the unresolved section of the report.

## Review first, mutate later

```bash
agent-thanks scan --repo . --session agent-session.log
agent-thanks review .agent-thanks-report.json
agent-thanks star .agent-thanks-report.json
```

This is useful when a report needs review by another person before any account
mutation.

## Star exact candidates only

```bash
agent-thanks star \
  .agent-thanks-report.json \
  --repo owner/first \
  --repo owner/second
```

Every requested repository must already exist in the report.

## Non-interactive verified-only run

```bash
agent-thanks star .agent-thanks-report.json --yes
```

`--yes` selects only high-confidence, meaningful-use candidates. Low-confidence
references require the separate and explicit `--all --yes` combination.

## Undo a completed batch

After adding Stars, the CLI prints a command containing only the repositories
that were newly starred:

```text
Undo this batch: agent-thanks unstar owner/one owner/two
```

Copy that command to restore the prior state. Repositories that were already
starred are excluded, so the receipt does not remove older Stars.

## Use a temporary configuration file

Linux and macOS:

```bash
AGENT_THANKS_CONFIG=/tmp/agent-thanks-config.json agent-thanks config --mode ask
```

PowerShell:

```powershell
$env:AGENT_THANKS_CONFIG = "$env:TEMP\agent-thanks-config.json"
agent-thanks config --mode ask
```

The configuration stores only the consent mode. It never contains a GitHub
credential.
