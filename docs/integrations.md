# Coding-agent integrations

`agent-thanks` can read coding-agent transcripts manually or receive hook events while an agent is working. Hooks only collect evidence and write local reports. They never authenticate to GitHub and never create Stars.

## Claude Code

### Plugin installation

Inside Claude Code:

```text
/plugin marketplace add dbwls99706/agent-thanks
/plugin install agent-thanks@agent-thanks
```

The plugin installs three hooks and one command. After a completed turn that ran shell commands, the stop hook scans the project and announces repositories that gained verified-use evidence in that session.

Example notice:

```text
agent-thanks: this task shows verified open-source use of BehaviorTree/BehaviorTree.CPP.
Review the evidence and approve Stars in a terminal: agent-thanks star .agent-thanks/reports/<session>-<hash>.json
```

`/thanks` shows the current report inside the session.

The `agent-thanks` executable must be on `PATH` for the plugin hooks to run.

### Manual Claude Code hook configuration

Without the plugin, add the following to `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "agent-thanks hook record --from claude-code"
          }
        ]
      }
    ],
    "PostToolUseFailure": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "agent-thanks hook record --from claude-code"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "agent-thanks hook stop --from claude-code"
          }
        ]
      }
    ]
  }
}
```

Claude Code provides a useful success contract: a matching successful `PostToolUse` event can verify the corresponding shell action. Explicit failure always wins over success.

## Codex CLI

Codex can run hooks from `~/.codex/hooks.json` or a project-local `.codex/hooks.json`.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "agent-thanks hook record --from codex"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "agent-thanks hook stop --from codex"
          }
        ]
      }
    ]
  }
}
```

Review and trust the hook definition inside Codex before relying on a project-local configuration.

A Codex hook entry becomes verified only when its result carries an explicit successful exit status. Missing or unjudgeable results remain references.

### Codex `notify` fallback

Without hooks, `notify` can invoke the stop handler:

```toml
# $CODEX_HOME/config.toml
notify = ["agent-thanks", "hook", "stop", "--from", "codex"]
```

The stop handler identifies the matching rollout by its working directory and session/thread identifier. If a transcript cannot be identified safely, the lookup fails instead of guessing.

## Gemini CLI

Gemini can invoke the stop hook through `AfterAgent`:

```json
{
  "hooks": {
    "AfterAgent": [
      {
        "hooks": [
          {
            "name": "agent-thanks",
            "type": "command",
            "command": "agent-thanks hook stop --from gemini"
          }
        ]
      }
    ]
  }
}
```

Gemini command evidence is currently review-only because its transcript format does not provide a positive success signal that `agent-thanks` accepts as proof. A failure can be observed, but absence of failure is not promoted to verified success.

If an `AfterAgent` hook does not fire in the installed Gemini version, run:

```bash
agent-thanks scan --from gemini
```

## Manual transcript discovery

You can skip hooks entirely and scan the newest transcript for the current project:

```bash
agent-thanks scan --from claude-code
agent-thanks scan --from codex
agent-thanks scan --from gemini
```

Default transcript locations:

| Agent | Default location |
| --- | --- |
| Claude Code | `~/.claude/projects/<project>/*.jsonl` |
| Codex | `$CODEX_HOME/sessions/**/rollout-*.jsonl`, defaulting to `~/.codex` |
| Gemini | `~/.gemini/tmp/**/*.json` |

`CLAUDE_CONFIG_DIR` and `CODEX_HOME` are honored.

Transcript lookup requires the recorded project directory to match the current project after normalization. Session identifiers must also agree when the agent format provides them. File names are never treated as proof of session identity.

## Hook state and privacy

Hook state is stored inside the project under:

```text
.agent-thanks/
├── sessions/
├── reports/
├── report.json
└── .gitignore
```

The directory ignores itself so raw session evidence is not accidentally committed.

On POSIX systems the state directory and files are tightened to owner-only permissions. A symbolic link anywhere in the private state path is refused. Session logs are pruned after 30 days.

The logs retain raw shell command text. If a secret was typed directly into a command, that secret can therefore appear in the local log. Delete `.agent-thanks/sessions` at any time to remove stored command history.

## What hooks are allowed to do

Hooks may:

- record supported shell activity,
- scan the project,
- write local reports,
- announce newly verified repository use.

Hooks may not:

- authenticate to GitHub,
- create or remove Stars,
- elevate review-only evidence into verified use,
- block the coding agent when `agent-thanks` itself fails.

Live Stars remain available only through an interactive terminal command:

```bash
agent-thanks star .agent-thanks/report.json
```
