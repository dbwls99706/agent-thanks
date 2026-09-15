---
description: Show the open-source repositories verified in this session and how to thank them with Stars
allowed-tools: Bash(agent-thanks review:*)
---

Run `agent-thanks review .agent-thanks/report.json` and show every candidate with
its evidence, preserving the `verified` and `review` markers.

The purpose of agent-thanks is to help the user notice open-source repositories
this coding task actually relied on and thank verified ones with a GitHub Star.
Explain that the evidence check protects that Star from becoming a noisy or
automated signal.

A Star must still be approved in an interactive terminal outside this session:

`agent-thanks star .agent-thanks/report.json`

Each eligible repository gets its own default-No prompt and a final confirmation.
Never try to star, unstar, authenticate, or bypass the interactive approval from
inside the agent session.

If the report does not exist yet, explain that the hook normally writes it after
a completed turn that ran shell commands. For a manual preview, suggest:

`agent-thanks thanks --from claude-code --dry-run`
