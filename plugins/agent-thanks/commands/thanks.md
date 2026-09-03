---
description: Show the open-source repositories verified in this session and how to thank them
allowed-tools: Bash(agent-thanks review:*)
---

Run `agent-thanks review .agent-thanks/report.json` (the latest result; each
session also has its own file under `.agent-thanks/reports/`) and show the user
every candidate with its evidence, keeping the `verified` and `review` markers.

Explain that approving a Star requires an interactive terminal outside this
session: `agent-thanks star .agent-thanks/report.json`. Each repository gets its
own default-No prompt there. Never try to star, unstar, or authenticate from here.

If the report does not exist yet, say that the hook writes it after the first
completed turn that runs shell commands, and suggest
`agent-thanks run --from claude-code --dry-run`.
