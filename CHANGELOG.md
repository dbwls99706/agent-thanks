# Changelog

All notable changes to this project will be documented in this file.

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
