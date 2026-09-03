# agent-thanks

[![Tests](https://github.com/dbwls99706/agent-thanks/actions/workflows/tests.yml/badge.svg)](https://github.com/dbwls99706/agent-thanks/actions/workflows/tests.yml)
[![Python 3.10–3.14](https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/dbwls99706/agent-thanks/blob/main/LICENSE)
<a href="https://smollaunch.com" target="_blank" rel="noopener"><img src="https://smollaunch.com/badges/featured.svg" alt="agent-thanks - Featured on Smol Launch" loading="lazy" width="150" height="36"></a>

Give open-source maintainers a visible thank-you when a coding agent
meaningfully uses their work.

<p align="center">
  <img src="https://raw.githubusercontent.com/dbwls99706/agent-thanks/main/docs/assets/agent-thanks-banner.svg" alt="AI: done in 12 seconds. Open source: 12 years in the making. Leave a star." width="900">
</p>

`agent-thanks` reconstructs the open-source footprint of one coding task:

1. Detect repositories from newly declared dependencies and from the commands a
   coding agent ran, read from its transcript or hook log.
2. Show the evidence behind every match.
3. Export a shareable Markdown record or approve eligible Stars one by one.

Detection uses deterministic rules and never calls another model. A plain URL is
treated as a reference, not proof of use, and a command counts as use only when
its successful completion is recorded. "Verified use" means exactly that: a use
whose success was directly confirmed, never an inference about influence. Every
live Star requires an explicit `y/N` decision in an interactive terminal; there
is no unattended or bulk-Star mode.

## Try it first

Install the CLI and run its built-in, read-only demo:

```bash
pipx install git+https://github.com/dbwls99706/agent-thanks.git
agent-thanks demo
```

Without `pipx` (Windows, conda, or a plain virtual environment), install the
latest release wheel or the current `main` archive with pip:

```bash
python -m pip install "https://github.com/dbwls99706/agent-thanks/archive/refs/heads/main.zip"
```

Release wheels and their SHA-256 checksums are attached to every
[GitHub Release](https://github.com/dbwls99706/agent-thanks/releases).

The install requires network access. The demo itself makes no network requests,
reads no credentials, writes no files, and changes no Stars. It includes both
verified use and a low-confidence reference so the boundary is visible.

## Start in 60 seconds

Preview one task without authenticating to GitHub:

```bash
agent-thanks run \
  --repo . \
  --base HEAD \
  --session path/to/agent-transcript.jsonl \
  --dry-run
```

This writes `.agent-thanks-report.json`, explains every candidate, and prints
which verified repositories would be offered for approval. The session file can
be an agent transcript (JSON or JSON Lines) or a plain-text command log; plain
text carries no results, so its commands stay review-only unless you add
`--trust-session` to attest that they succeeded. Review or export the
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
  - Session ran a repository-use command that completed successfully (session.jsonl:8)
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
| Verified use | A newly declared direct dependency, a clone, Git install, or submodule command whose successful completion is recorded, or an explicit provenance statement | Eligible for a default-No `y/N` prompt |
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
| Agent transcript or hook log | Clone, submodule, or Git install commands with a recorded successful result; line-initial provenance in agent prose | High |
| Plain-text session log | The same commands without any recorded result | Low unless `--trust-session` attests success |
| Plain GitHub URL in a session log, transcript, or agent prose | Any public GitHub repository | Low; review only |

Session commands that can count as use:

- `git clone` and `gh repo clone`
- `git submodule add`
- Git-based `pip`, `uv`, npm, pnpm, Yarn, Cargo, and Go commands

A command counts only when a recorded success belongs to it. The statement must
be a single command, or a chain joined only by `&&` whose other segments are
trivially safe (`cd`, `mkdir`, `echo`, and similar; not `set`, `export`,
`printf`, or a variable assignment), and every executable must be named
without a path, because `/tmp/fake/git` or `./git` proves nothing about the
real tool. `env PATH=... git clone URL`,
`git clone URL || true`,
`git clone URL; echo ok`, `git clone URL | tee log`, `git clone URL &`,
`eval 'exit 0' && git clone URL`, `git clone URL && make`, and a tool call that
runs several lines can exit successfully while the clone failed or never ran,
so they stay references. Provenance lines that start with `copied from`,
`adapted from`, or `used code from` and name the repository as their direct
target count as use on their own, in agent prose or a plain-text log; the text
of a tool command is never read as provenance.

Manifest evidence is separate: a newly declared direct dependency counts because
the project now declares it, not because an install is known to have succeeded.
The report keeps the two kinds apart (`direct_dependency` versus
`session_usage`).

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
agent-thanks run --repo . --base HEAD --session transcript.jsonl --dry-run
```

### Work already committed

Point `--base` to the revision immediately before the task:

```bash
agent-thanks run --repo . --base HEAD~1 --session transcript.jsonl --dry-run
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
agent-thanks scan --repo . --session transcript.jsonl --offline
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

## Coding agent integration

Agents such as Claude Code, Codex CLI, and Gemini CLI clone repositories and
install packages on your behalf. `agent-thanks` can read what they did in two
ways, and neither interrupts the agent or changes a Star:

1. **Transcripts.** Pass the agent's session transcript with `--session`; JSON
   and JSON Lines files are detected automatically. A command counts only when
   a known shell tool (`Bash`, `shell`, `exec_command`, `run_shell_command`,
   and similar) that the agent itself called, at the position its agent
   writes calls (a content item of a Claude Code assistant message, a Codex
   response item, a part of a Gemini model turn), executed it, the transcript
   records an exact success for that call in the position and field its agent
   writes (`is_error` equal to `false` in a Claude Code `tool_result` inside a
   user message, or an exit code of 0 in the JSON or header envelope Codex
   writes for its own `shell` or `exec_command` tool; Gemini records no
   success signal) after the call and with no failure signal anywhere in the
   envelope, the envelope is small enough to scan completely and every JSON
   object in it names each key once, every item's own role agrees with the
   message and record around it, every transcript of the same recorded session
   and project scanned together agrees about the call, the call id is used
   for that call alone, the transcript has no unparsable line, the call names
   the agent's own shell tool (`Bash` for Claude Code, `shell` or
   `exec_command` for Codex), a corrupted transcript vouches for no success in
   another file, the
   call is a single logical line, and the statement is a single command or an
   `&&` chain whose other segments are trivially safe (`cd`, `mkdir`, `echo`,
   and similar). Program output and bare text can never supply a success
   signal, several results for one call combine failure first, and
   call-shaped objects inside user or tool content are never actions. A
   failed or conflicting result, a result with no signal, a missing result, a
   transcript that records no results at all, a multi-line invocation, or a
   call to any other tool only ever produces references, and the evidence says
   which case applied. In the agent's prose,
   only a line-initial provenance statement such as
   `Adapted from https://github.com/owner/repository` counts as use, and only
   when the text sits at a message position whose role is the assistant's;
   text under a user, system, or developer role, under a conflicting role, or
   nested anywhere else is a review-only reference. Every other URL, including
   commands quoted in Markdown code fences, stays a review-only reference. Tool
   output, your prompts, and hidden reasoning are never treated as actions.
2. **Hooks.** `agent-thanks hook record` appends every executed shell command
   to `.agent-thanks/sessions/<session>-<hash>.jsonl` as a structured entry with its
   recorded `status` (`ok`, `error`, or `unknown`) and the `basis` for it. A
   failure always wins: an explicit failure in the payload or a Claude Code
   `PostToolUseFailure` event records `error`. A success is recorded only
   through one of two contracts, and the contract is never inferred from the
   payload: Claude Code with `--from claude-code`, a `PostToolUse` event, and
   the `Bash` tool, whose event fires only after a successful run; or Codex
   with `--from codex`, a `PostToolUse` event, its canonical `Bash` tool, and
   an explicit exit status of 0 in the response. Gemini has no success
   contract. Entries carry a schema marker, the agent, the event, the tool,
   and the tool call id; a stored entry counts as a success only when those
   fields still form one of the two contracts, a `PreToolUse` payload is never
   recorded, and a log with any corrupted line promotes nothing.
   `agent-thanks hook stop` reads this log as the authority for actions:
   several entries for one tool call combine failure first, the log's status
   overrides the transcript's own result for a call only when the call id and
   the exact command text both match and the transcript recorded no failure
   for it (a failure whose call record is missing from a partial transcript
   still counts), a mismatch or a recorded failure demotes the hook entry and
   the transcript command alike, a call id the transcript reuses for different
   calls is ambiguous, and a transcript command the log never saw stays
   unconfirmed. The transcript is merged for prose
   provenance and for the calls the log confirms. The hook promotes only `ok`
   entries, writes
   `.agent-thanks/reports/<session>-<hash>.json` (and a copy at
   `.agent-thanks/report.json` as the latest result), and announces
   repositories the first time they show verified use in that session. Logs
   and reports older than 30 days are pruned, and the directory ignores itself
   through its own `.gitignore`. Hooks exit successfully even when something
   goes wrong, so they can never block the agent, and with `--from codex` or
   `--from gemini` they print `{}` whenever they have nothing to say, because
   those agents parse a hook's standard output as JSON. On POSIX systems the
   state directory and every file in it are created readable by their owner
   only (`0700` and `0600`) and tightened on every run, because the log keeps
   the raw text of every shell command for 30 days, secrets included; delete
   `.agent-thanks/sessions` at any time to drop it. Windows keeps the profile's
   own access control instead. A symbolic link anywhere in the state directory
   is refused, so the hooks can neither write through nor prune through one.
   Every supported hook
   contract carries a session or thread identifier, which scopes the log, the
   report, and the announcements; a payload without one is scoped by its
   transcript path, and without either nothing is recorded and nothing is
   announced. Scopes become file names through a sanitized prefix plus a hash
   of the whole scope, so two sessions never share a log or a report.

Find the newest transcript for the current project without knowing its path:

```bash
agent-thanks run --from claude-code --dry-run
agent-thanks scan --from codex --output -
agent-thanks scan --from gemini
```

| Agent | Where transcripts live | `--from` lookup |
| --- | --- | --- |
| Claude Code | `~/.claude/projects/<project-path-with-dashes>/*.jsonl` | Newest file for the project directory |
| Codex CLI | `$CODEX_HOME/sessions/**/rollout-*.jsonl` (default `~/.codex`) | Newest file whose recorded directory equals the project |
| Gemini CLI | `~/.gemini/tmp/**/*.json` | Newest file whose recorded directory equals the project |

`CLAUDE_CONFIG_DIR` and `CODEX_HOME` are honored. Every candidate must record
the project directory itself, and directories are compared exactly after
normalization, never by substring or by the encoded folder name alone; a hook
payload that carries a session or thread identifier must match the identifier
the transcript records, and a file name never stands in for it. A lookup that
cannot confirm the project fails instead of guessing; pass the file with
`--session` then.

### Claude Code plugin

The repository doubles as a plugin marketplace. Inside Claude Code:

```text
/plugin marketplace add dbwls99706/agent-thanks
/plugin install agent-thanks@agent-thanks
```

The plugin installs three hooks and one command. After each completed turn that
ran shell commands, the `Stop` hook scans the project and, when a repository
shows verified use for the first time, shows a one-line notice such as:

```text
agent-thanks: this task shows verified open-source use of BehaviorTree/BehaviorTree.CPP.
Review the evidence and approve Stars in a terminal: agent-thanks star .agent-thanks/reports/<session>-<hash>.json
```

`/thanks` shows the current report inside the session. Approval still happens
in your own terminal with `agent-thanks star .agent-thanks/report.json`, one
default-No prompt per repository. The `agent-thanks` command must be on `PATH`
for the hooks to run.

Without the plugin, the same hooks work from `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "agent-thanks hook record --from claude-code" }] }],
    "PostToolUseFailure": [{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "agent-thanks hook record --from claude-code" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "command": "agent-thanks hook stop --from claude-code" }] }]
  }
}
```

### Codex CLI and Gemini CLI

Support for these agents rests on the fields their hooks and transcripts
actually record. Nothing is promoted on guesswork, so a format that records no
exact success yields references only.

Codex CLI runs hooks from `~/.codex/hooks.json` (or `.codex/hooks.json` inside
the project) with the same event names and payload fields as Claude Code, and
its payloads name the shell tool `Bash`:

```json
{
  "hooks": {
    "PostToolUse": [{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "agent-thanks hook record --from codex" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "command": "agent-thanks hook stop --from codex" }] }]
  }
}
```

Codex runs a hook only after you review and trust its exact definition: run
`/hooks` inside Codex to inspect, trust, or disable hooks, and note that a
project-local `.codex/hooks.json` loads only when the project's `.codex` layer
is trusted. The stop payload carries `session_id`, `cwd`, and
`transcript_path`, so no lookup is needed. Because the Codex post-tool event
also fires for commands
that exit with a non-zero status, a Codex entry is `ok` only for a
`PostToolUse` event of the `Bash` tool whose
`tool_response` carries a successful exit status: the JSON envelope of the
`shell` tool (`metadata.exit_code`) or the header the `exec_command` tool
writes ahead of the program output (`Process exited with code 0`). Anything
else is recorded as `error` or `unknown` and never promoted. The transcript
adapter reads Codex `function_call` and `custom_tool_call` records for
`shell` and `exec_command` the same way; a call whose recorded result is still
running, missing, or unjudgeable stays a reference. Codex transcript support is
best effort until a real rollout has been tested against it; the hook path is
the supported one. A code-mode `exec` call
records a program rather than a shell command, so its text yields references
only; Codex applies hooks to the tool calls that program makes, which is why
the hook log, not the transcript, covers Work Mode sessions.

Without hooks, point `notify` at the stop hook:

```toml
# $CODEX_HOME/config.toml
notify = ["agent-thanks", "hook", "stop", "--from", "codex"]
```

Codex appends its notification payload as the last argument. The payload
carries the working directory and thread identifier but no transcript path, so
`--from codex` selects the rollout under `$CODEX_HOME/sessions` whose recorded
directory equals the working directory and whose session identifier equals the
thread. The report lands in `.agent-thanks/reports/<thread>-<hash>.json`; review
it later with `agent-thanks star` on that file.

Gemini CLI can run the stop hook from an `AfterAgent` hook in
`~/.gemini/settings.json`, and `--from gemini` reads its transcripts under
`~/.gemini/tmp`:

```json
{
  "hooks": {
    "AfterAgent": [{ "hooks": [{ "name": "agent-thanks", "type": "command", "command": "agent-thanks hook stop --from gemini" }] }]
  }
}
```

Gemini CLI currently yields review-only references. Its shell tool records a
failure explicitly (an `Exit Code` line and `isError`) but marks success only
by the absence of those signals, and absence is never accepted as success.
Repositories it cloned still appear in the report for review; verified use
needs a recorded success that Gemini does not write yet. Gemini's `AfterAgent`
hook has not fired reliably in every version, so when a turn ends without a
report, run `agent-thanks scan --from gemini` by hand; it reads the same
transcript under `~/.gemini/tmp`.

Automation stops at detection by design. No hook, plugin, or transcript flag
authenticates to GitHub or changes a Star.

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

Discovery of the draft `ATTRIBUTION.md` v0.1 protocol is on the roadmap; v0.5.0
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
agent-thanks hook     Detection-only entry points for coding-agent hooks
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
