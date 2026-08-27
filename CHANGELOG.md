# Changelog

All notable changes to this project will be documented in this file.

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
