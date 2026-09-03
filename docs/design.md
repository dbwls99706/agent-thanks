# Design notes

## Product boundary

`agent-thanks` reports repositories observable during one coding task. It does
not claim to identify repositories in a model's training data or infer invisible
influences.

The current release recognizes two evidence families and keeps them apart:

1. A direct dependency declared, or repinned to a different repository source,
   since a Git baseline. This says the project now declares the dependency; it
   does not say an install succeeded.
2. A repository command whose successful completion is recorded in an agent
   transcript or hook log, or an explicit provenance statement. A plain-text
   command log records no results, so its commands stay references unless the
   user attests their success with `--trust-session`.

One rule governs every command: if the success of the repository command
itself cannot be directly confirmed, it is never verified use. A recorded
success belongs to a command only when the statement is a single command, or a
chain joined solely by `&&` whose every segment is a repository command or a
trivially safe command (`cd`, `pushd`, `popd`, `mkdir`, `true`, `echo`,
`pwd`). Statements using `;`, `||`, `|`, or `&`, chains containing any other
command (`exit`, `eval`, `source`, `builtin`, `set`, `export`, `printf`, a
variable assignment, `make`, an unknown executable), an `env` wrapper that
carries an assignment or option, and tool invocations that span several
logical lines can exit successfully while the repository command failed or
never ran, so they stay references.

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

`agent-thanks hook record` and `agent-thanks hook stop` are the same kind of
operation. They exist so coding agents can trigger detection after a turn
without interrupting the agent: `record` appends executed shell commands to
`.agent-thanks/sessions/<session>-<hash>.jsonl`, and `stop` scans the project,
writes `.agent-thanks/reports/<session>-<hash>.json` plus a latest copy at
`.agent-thanks/report.json`, and prints a one-line notice the first time a
repository shows verified use in that session. Hooks never authenticate, never
contact GitHub, and always exit successfully, so a failure inside a hook cannot
block the agent or approve anything. Approval remains the interactive `star`
command.

`record` writes a structured entry per command with a `status` and a `basis`.
An explicit result in the `tool_response` decides the status. Without one, the
status is `unknown` unless the hook was started with `--from claude-code`: the
Claude Code post-tool event fires only after a successful run, so that contract
records `ok` with basis `successful_post_tool_event`. The contract is never
inferred from the payload, because hook payloads from different agents share
the same field names and the Codex post-tool event also fires for failed
commands. Every entry carries the hook log schema marker, the agent, the hook
event, the tool, the tool call id, the command, the status, and the basis; a
payload whose event is not a post-tool event is never recorded. A failure
always wins: an explicit failure in the response or a Claude Code
`PostToolUseFailure` event records `error`. A success is recorded, and later
counted, only through a row of the promotion matrix: Claude Code, `PostToolUse`,
`Bash`, and the successful-event basis; or Codex, `PostToolUse`, `Bash`, and an
explicit exit status of 0. Gemini has no row, because its hooks define no
success signal. `stop` treats the hook log as the authority for actions. A
file without the schema marker is not a hook log, whatever keys it has, and a
log with any corrupted or foreign line keeps its failures but proves no
success. An entry's success counts only when the entry is complete: a matrix
row, a non-empty tool call id, and a non-empty command. Entries that share a tool call id combine failure
first, in the log's own evidence as well as in the override, so a call
recorded as failed once never counts, and entries that disagree about their
command are never ok. The log's status replaces the transcript's own result
for a call only when the call id and the exact command text both match; a
call whose recorded command differs from the log's is a conflict, a transcript
command the log never saw stays unconfirmed, and only `ok` entries are
promoted. The demotion is symmetric: a hook entry whose call id the transcript
records with a different command, reuses for different calls, or records with
an explicit failure is demoted too, and a failure whose call record is missing
from a partial transcript still counts, so a disagreement between the two
sources never leaves a success standing on either side. The scanner applies
these rules whenever a hook log and a transcript are scanned together, from
the hooks or from `scan --session`. The transcript is merged for prose
provenance and for the calls the log confirms. With `--from codex` or
`--from gemini` the hooks print `{}` when they have nothing to say, because
both agents parse a hook's standard output as JSON.

Agent transcripts are read with a narrower rule than shell logs. Every
recorded tool result is judged as `ok`, `error`, or `unknown` with failure
first: any explicit failure signal (`is_error` true, a non-zero exit code, a
non-empty `error`, a failure status, or an "Exit code: N" line with N != 0,
wherever it appears) makes the result `error` even when a success signal is
also present; without a failure signal only an exact success signal makes it
`ok`; everything else, including a result with no signal at all, is `unknown`.
Success signals are read only from the structured field each agent writes, and
only in the position that agent writes it. A Claude Code `tool_result` counts
only inside a user-role message, paired with a `tool_use` call of Claude Code's
own `Bash` tool, and only its top-level `is_error` equal to `false` is a
success. A Codex output item counts only without a role, paired with a Codex
call record (`function_call` or `custom_tool_call`) of one of Codex's own shell
tools (`shell`, `exec_command`), and only an `exit_code` of 0 at the top level
or under `metadata` is a success: in a result object, in the JSON-encoded
envelope, or as the exit code in the header block that precedes the `Output:`
marker (`Exit code: 0` or `Process exited with code 0`, with every header line
a header field). A Gemini `functionResponse` counts only inside a user-role
message paired with a `functionCall`, and Gemini defines no success signal. A
result anywhere else, or paired with a call of another kind or tool, keeps its
failure signals but never yields a success; so does a success recorded before
the call it names and a result in a record whose outer type and inner message
role disagree. Failure signals, by contrast, are collected from the whole
envelope at any depth: every `is_error` or `isError` that is true or of an
unexpected type, every exit code field that is non-zero or not an integer,
every non-empty `error`, every failure `status` or status of an unexpected
type, and every non-zero "Exit code" text, nested JSON strings included, so one
contradictory or malformed field anywhere blocks the success. A call id that the transcript
reuses for different calls attributes no result to any of them, and a
transcript with an unparsable line is corrupted: its commands stay references
whatever its results say, while a hook log scanned with it stands on its own
record. Bare text from any other tool, program output (`content`,
`output`, `stdout`, and similar), and a `status` field can never supply a
success signal, so a program that prints a success message, a fake header, or
a success JSON cannot fake one. Results are indexed only from envelope
positions, never from result-shaped objects inside program output, and
several results for one call combine failure first. The same judge applies to
every format; the only format knowledge it carries is which tool wrote an
envelope and where that envelope ends. A command counts
like a shell-log line only when the agent itself called a tool on the exact
allowlist of known shell tools (call-shaped objects inside user or tool
content are never actions), the call's result is `ok`, the invocation is a
single logical line, and the statement is pure. Failed, unknown, and missing
results, transcripts that record no results at all, multi-line invocations,
and calls to any other tool contribute references only; the evidence detail
names the reason. In the agent's prose only a line-initial provenance
statement counts as use. Tool output, user prompts, and hidden reasoning are
never treated as actions. Prose counts only at a message position whose role is
on the assistant side; text under a user, system, or developer role, under a
conflicting role, or nested deeper than the message content is a reference.

## Promotion gate

A command becomes verified use only when every condition below holds; each is
enforced in one place, and changing any one of them in a recorded session must
leave the command a reference:

1. A verified agent, event, tool, and schema: a hook entry needs the schema
   marker and an (agent, event, tool, basis) row of `HOOK_PROMOTION_MATRIX`,
   checked when it is recorded (`_hook_outcome`) and again when it is read
   (`hook_entry_status`), inside a log without corrupted lines
   (`_hook_log_entries`); a transcript result needs its agent's position, its
   agent's call kind, and its agent's success field (`_result_of`,
   `result_status`), inside a transcript without unparsable lines
   (`load_transcript`).
2. A non-empty tool call id indexed at an envelope position with exactly this
   call's fingerprint and for this call alone, under a role that is not on the
   user side and does not contradict its record; a call found anywhere else,
   or with another fingerprint, claims nothing, and a success result must
   follow the call it names (`_index_calls`, `_index_results`,
   `_command_outcome`).
3. The identical command text, compared after removing outer whitespace only,
   in the hook entry and the transcript call when both exist, enforced on both
   sides (`canonical_command`, `_command_outcome`, `scan_hook_log_evidence`,
   `combine_hook_entries`).
4. A single logical line (`scan_session_evidence` with `single_statement`).
5. An allowed result envelope with the agent's success field at its fixed
   position and no contradictory or malformed field anywhere inside it
   (`result_status`, `_agent_success`, `_failure_signals`); provenance
   phrases never count inside a command (`scan_session_evidence` with
   `provenance` off) and count in prose only at an assistant-role message
   position (`iter_transcript_records`).
6. A shell structure that lets the recorded result be attributed to the
   repository command: a single command or a pure `&&` chain of trivially safe
   segments, without `env` assignments, `set`, `export`, `printf`, or variable
   assignments (`analyze_command_line`).
7. No conflicting signal: every result and every hook entry for the call, from
   every source scanned together, combines failure first, including a failure
   result whose call record is missing (`combine_statuses`,
   `combine_hook_entries`, `transcript_calls`, `scan_hook_log_evidence`,
   `_command_outcome`).

Every supported hook contract carries a session or thread identifier. It
becomes the scope of the log, the report, and the announcements; a payload
without one is scoped by its transcript path, and a payload with neither is
not recorded and never announced, because its commands could not be kept apart
from another session's. Scopes become file names through a sanitized prefix
plus a hash of the whole scope, so no two scopes share a file.

The state directory and every file in it are created readable by their owner
only and tightened on each run, because the hook log keeps the raw text of every
shell command, secrets included, for 30 days.

Transcript lookups for `--from` return a file only when the project directory
it records equals the current directory after normalization, and, when the
hook payload names a session or thread, only when that identifier matches as
well; otherwise they fail and ask for an explicit `--session`. Hook state is
scoped to the agent session: each session gets its own command log, its own
report, and its own announcement history, so a repository used again in a
later session is announced again and an announcement always points at the
report it describes.

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
