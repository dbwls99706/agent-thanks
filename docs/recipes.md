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
  --session transcript.jsonl \
  --dry-run
```

Staged, unstaged, and untracked supported manifests are included.

## Scan one committed task

If the task is the most recent commit:

```bash
agent-thanks run \
  --repo . \
  --base HEAD~1 \
  --session transcript.jsonl \
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
  --session planning.jsonl \
  --session implementation.jsonl \
  --session review.jsonl
```

Evidence for the same repository is deduplicated into one candidate.

## Attest a plain-text command log

A plain-text log records commands but not their results, so its commands stay
review-only by default. When the log is your own shell history and you can
vouch that the commands succeeded, attest it explicitly:

```bash
agent-thanks scan --repo . --session shell-history.log --trust-session
```

Even then, only single commands and pure `&&` chains count; `git clone URL ||
true` and similar statements remain references.

## Scan a coding agent transcript

Transcripts are detected automatically when the file is JSON or JSON Lines:

```bash
agent-thanks scan --repo . --session ~/.claude/projects/-home-me-project/abc123.jsonl
agent-thanks scan --repo . --session ~/.codex/sessions/2026/09/02/rollout-2026-09-02T10-00-00-abc.jsonl
```

Let the tool find the newest transcript for the current project:

```bash
agent-thanks run --from claude-code --dry-run
agent-thanks scan --from codex --output -
agent-thanks scan --from gemini
```

Commands the agent executed count only when the transcript records a
successful result for that call. In the agent's prose only a line-initial
`Adapted from https://github.com/owner/repository` counts as use; other URLs
remain review-only references.

## Record commands with agent hooks

Claude Code users can install the plugin bundled with this repository:

```text
/plugin marketplace add dbwls99706/agent-thanks
/plugin install agent-thanks@agent-thanks
```

Codex CLI users can run the record and stop hooks from `~/.codex/hooks.json`; Codex
hook payloads name the shell tool `Bash`:

```json
{
  "hooks": {
    "PostToolUse": [{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "agent-thanks hook record --from codex" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "command": "agent-thanks hook stop --from codex" }] }]
  }
}
```

Codex runs these only after you review and trust them with `/hooks` inside
Codex.

Without hooks, point `notify` in `$CODEX_HOME/config.toml` at the stop hook:

```toml
notify = ["agent-thanks", "hook", "stop", "--from", "codex"]
```

Gemini CLI users can run the stop hook after each turn from
`~/.gemini/settings.json`; Gemini results are review-only until Gemini records
an explicit success:

```json
{
  "hooks": {
    "AfterAgent": [{ "hooks": [{ "name": "agent-thanks", "type": "command", "command": "agent-thanks hook stop --from gemini" }] }]
  }
}
```

After a completed turn that ran shell commands, the report is in
`.agent-thanks/report.json`. Approve Stars from a terminal:

```bash
agent-thanks star .agent-thanks/report.json
```

## Read a session log from standard input

```bash
cat implementation.log | agent-thanks scan --repo . --session -
```

Standard input stays local. Use a separate interactive `star` command after
reviewing the saved report.

## Work without registry access

```bash
agent-thanks run --repo . --session transcript.jsonl --offline --dry-run
```

Direct GitHub URLs, Git submodules, and GitHub-hosted Go modules still resolve.
Package names requiring PyPI, npm, or crates.io metadata remain unresolved.

## Review now, decide later

```bash
agent-thanks scan --repo . --session transcript.jsonl
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
