# Security policy

## Supported versions

Security fixes are provided for the latest released minor version.

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| 0.1.x | No |

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in a public issue. Use GitHub's
private vulnerability reporting from the repository's **Security** tab.

Include the affected version, reproduction steps, expected impact, and any
suggested mitigation. Reports will be acknowledged as soon as practical. A fix
and coordinated disclosure plan will be prepared before public discussion.

## Credential handling

`agent-thanks` does not persist GitHub credentials. It reads an existing GitHub
CLI session or process environment at execution time. Reports and session logs
may still reveal local paths or dependency names and should be handled as
potentially sensitive project metadata.
