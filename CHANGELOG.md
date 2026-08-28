# Changelog

All notable changes to this project will be documented in this file.

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
