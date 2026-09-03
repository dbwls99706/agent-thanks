# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

- Ship `examples/session.jsonl` in the source distribution again. The packaging
  list still named the `examples/*.log` file that 0.5.0 replaced, so source
  distributions carried no example at all; a release check now fails when the
  bundled example is missing.
- Name the evidence source in the README walkthrough after the transcript the
  documented commands actually scan, instead of a file name no run produces.

## 0.5.0 - 2026-09-03

- Count a session command as verified use only when its own successful
  completion is recorded: the recorded result must carry an exact success
  signal and no failure signal, the tool call must be a single logical line,
  and the statement must be a single command or an `&&` chain whose other
  segments are trivially safe (`cd`, `mkdir`, `echo`, and similar; `set`,
  `export`, `printf`, variable assignments, and `env` wrappers with
  assignments or options are not, because `set -n` skips execution and an
  assignment can redirect `PATH` to a fake `git`), and every executable must
  be named without a path. Failed,
  conflicting, unjudgeable, and missing results, transcripts without results,
  multi-line invocations, and compound statements such as
  `git clone URL || true` or `eval 'exit 0' && git clone URL` stay references,
  and the evidence names the reason.
- Treat plain-text session logs as review-only by default, because they record
  no results; `--trust-session` attests that their commands succeeded.
- Read coding-agent transcripts (JSON and JSON Lines) with `--session`,
  pairing recognized shell-tool calls in the supported structured formats
  (Claude Code, Codex CLI, and Gemini CLI records) with their recorded
  results. Success is read only from the structured field each agent writes,
  in the position it writes it (a Claude Code `tool_result` inside a user
  message, a Codex output item paired with a Codex call record of its own
  `shell` or `exec_command` tool; Gemini has none), only for calls and results
  at the positions the agent writes them, only for the agent's own shell tool
  (`Bash` for Claude Code), only after the call it names, only when every
  item's own role agrees with its record, only when the envelope can be
  scanned completely and no contradictory or malformed field anywhere in the
  whole result record blocks the success, and only when every transcript of
  the same recorded session and project scanned together agrees about the
  call, never from program output or bare text; every JSON record and
  JSON-encoded result is parsed rejecting duplicate keys; a transcript with an
  unparsable line promotes nothing, in any file; provenance prose counts only
  at an assistant message position; several
  results for one call combine failure first; a call id reused for different
  calls, or a call found outside a recognized tool call position, attributes
  no result; call-shaped objects inside user or tool content are never
  actions; provenance phrases count in prose only, never inside a command.
  Only known shell
  tools count; other tools contribute references. In agent prose only
  line-initial provenance statements count as use. Tool output, user prompts,
  and hidden reasoning are never actions.
- Recognize Codex `exec_command` results, whose exit code is recorded in a
  header ahead of the program output, and Codex hooks, whose payloads name the
  shell tool `Bash`. Code-mode `exec` programs yield references only; the
  hook log covers the calls they make. Gemini CLI yields review-only
  references, because its
  shell tool marks success only by the absence of failure signals.
- Replace the bundled plain-text example with `examples/session.jsonl`, a
  transcript whose classification matches `agent-thanks demo`.
- Add `--from claude-code|codex|gemini` to `scan`, `run`, and `hook stop` to
  locate the transcript whose recorded project directory equals the current
  one and whose recorded session identifier matches the hook payload exactly,
  never by file name alone, honoring `CLAUDE_CONFIG_DIR` and `CODEX_HOME`, and
  to fail rather than guess.
- Create the `.agent-thanks` state directory and its files readable by their
  owner only on POSIX, tightening every known file on each run, because hook
  logs keep raw shell commands; refuse symbolic links and special files
  anywhere in the state directory for reads and writes alike, write whole
  files through a private temporary file, and prune only regular files inside
  it.
- Add `agent-thanks hook record` and `agent-thanks hook stop`, detection-only
  entry points for agent hooks. `record` keeps a structured per-session log
  with each command's recorded status and basis, treating the Claude Code
  success-only post-tool event as a success basis only when started with
  `--from claude-code` and only for a `PostToolUse` payload of the `Bash` tool,
  never recording pre-tool events; a Codex entry is `ok` only for a
  `PostToolUse` payload of its canonical `Bash` tool with an explicit exit
  status of 0; a Claude Code `PostToolUseFailure` payload records an error;
  Gemini has no success contract. Every entry carries a schema marker, the
  agent, the event, the tool, and the tool call id; a stored success counts
  only while those fields still form one of the two contracts, and a log with
  any corrupted line promotes nothing.
  `stop` treats that log as the authority for actions, combining several
  entries for one call failure first, overriding the transcript's own result
  only when call id and exact command text both match and the transcript
  recorded no failure, leaving mismatches and transcript commands the log
  never saw unconfirmed (a failure whose call record is missing from a partial
  transcript still counts), promotes successful entries only, writes
  per-session reports, and announces newly verified repositories once per
  session, without ever changing a Star. With `--from codex` or `--from
  gemini` the hooks answer `{}` when silent, as those hook contracts require.
- Bundle a Claude Code plugin marketplace with hooks and a `/thanks` command.
- Scope hook logs, reports, and announcements by the session or thread
  identifier every supported hook contract carries, or by the transcript path
  when a payload lacks one; a payload with neither is not recorded and never
  announced. File names are a sanitized prefix plus a hash of the whole scope,
  so distinct sessions never share a file. Commands are stored and compared
  with outer whitespace removed.
- Document pip and release-wheel installation for environments without `pipx`.

## 0.4.2 - 2026-09-02

- Restore repository detection for editable VCS requirements such as
  `-e git+https://github.com/owner/repository.git#egg=name`, which 0.4.1 left
  unresolved.
- Never map a dependency through PyPI, npm, or crates.io when its manifest pins
  a Git, URL, local path, workspace, or alternative-registry source. Such
  dependencies count only when the pinned source itself names a GitHub
  repository and are otherwise reported as unresolved.
- Ignore local path requirements such as `vendor/pkg` instead of reading them
  as GitHub `owner/repository` shorthand.
- Treat an existing dependency that is repinned to a different repository
  source as new use of that repository, with evidence that names the source
  change.
- Validate evidence confidence and meaningful-use fields when reading a report,
  and report malformed JSON reports as errors instead of a traceback.

## 0.4.1 - 2026-09-02

- Match high-confidence session evidence to the exact repository targeted by a
  supported command or provenance phrase, leaving nearby URLs as review-only
  references.
- Parse shell options, non-mutating flags, command reachability, comments,
  document examples, heredocs, and line continuations conservatively so
  ambiguous session text fails closed.
- Recognize repository operands in `gh repo clone` and package-manager GitHub
  shorthand without weakening the per-repository evidence boundary.
- Require direct, valid GitHub targets in commands and package metadata instead
  of promoting GitHub text embedded inside unrelated or malformed URLs.

## 0.4.0 - 2026-08-28

- Require a real interactive terminal and a default-No decision for every live
  Star; the authenticated GitHub account is shown before approval.
- Check existing Stars before prompting, so one account is not asked to approve
  the same repository again.
- Remove persistent `ask`/`auto` configuration and all unattended Star paths,
  including `--mode`, `--yes`, and `--all --yes`.
- Make low-confidence and viewed-only references ineligible for Star even when
  requested explicitly with `--repo`.
- Keep detection, JSON reports, review, Markdown export, demo, and dry-run
  workflows non-interactive and free of Star mutations.
- Add `agent-thanks export` for deterministic, shareable Markdown evidence with
  optional review-only references and sanitized absolute source paths.
- Require interactive confirmation for Unstar operations and preserve exact Undo
  receipts for new Stars and partial failures.
- Ignore legacy 0.3.x consent configuration safely and document migration to
  evidence-only automation.
- Align README, design notes, recipes, troubleshooting, terminal visuals, CI,
  and package metadata with the new human-confirmation invariant.
- Publish wheel and source distributions with SHA-256 checksums in the GitHub
  Release workflow.

## 0.3.1 - 2026-08-28

- Add `agent-thanks demo`, a credential-free and network-free preview of the
  evidence, review, and dry-run experience.
- Verify the demo from the built wheel in CI.
- Clarify the task-level positioning relative to whole-project dependency
  starring tools.
- Document that `ATTRIBUTION.md` v0.1 parsing is planned rather than currently
  implemented, and that its `mode: suggest` requires per-repository consent.

## 0.3.0 - 2026-08-27

- Prevent renamed or relocated manifests from making existing dependencies look
  newly introduced.
- Compare dependency changes against the full Git baseline so a dependency
  moved between manifests is not recommended twice.
- Keep dry-run operations completely detached from GitHub authentication.
- Add live-compatible coverage for missing credentials and GitHub 401, 403, and
  404 failures without reporting false success.
- Add `agent-thanks doctor` to verify Python, Git, project state, consent mode,
  and the authenticated GitHub account before the first Star.
- Check existing Star state so repeated runs report `Already starred` without
  claiming a new mutation.
- Print an exact one-command Undo receipt for every newly completed batch,
  including API and network failures after partial progress.
- Add direct Go module and Git submodule detection.
- Recognize bare `github.com/owner/repository` references in agent transcripts.
- Test Python 3.10 through 3.14 plus Windows and macOS smoke environments.
- Add a 60-second onboarding path, usage recipes, troubleshooting guidance,
  an accessible terminal walkthrough, and a GitHub social preview.

## 0.2.0 - 2026-08-26

- Add persistent `ask` and `auto` consent modes with interactive first-run setup.
- Add `agent-thanks config` for setting, changing, and inspecting consent policy.
- Add `agent-thanks run` to scan and apply the selected policy in one command.
- Add per-run `--mode ask|auto` overrides that do not mutate saved settings.
- Make every prompt in `ask` mode default to No and retain final batch approval.
- Restrict `auto` to verified meaningful-use candidates; viewed-only and
  low-confidence references remain unstarred.
- Save configuration atomically with owner-only POSIX permissions.
- Add safe non-interactive error handling and Ctrl-C cancellation.
- Expand the test suite for setup, persistence, overrides, and both consent modes.
- Add issue forms, pull request guidance, security policy, code ownership, and
  dependency update configuration for the public repository.

## 0.1.0 - 2026-08-26

- Add Git-aware dependency scanning for Python, Node.js, and Rust manifests.
- Add agent-session GitHub repository detection with meaningful-use evidence.
- Add PyPI, npm, and crates.io repository metadata resolution.
- Add review, consent-gated star, dry-run, and unstar commands.
- Add Python 3.10–3.13 test workflow and standard-library test suite.
