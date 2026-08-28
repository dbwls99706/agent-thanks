# Troubleshooting

Run this first:

```bash
agent-thanks doctor --repo .
```

It shows the active GitHub account and catches most local setup problems without
changing any Star.

## Authentication required

```text
Authentication required. Run 'gh auth login' or set GH_TOKEN with Starring:
write permission.
```

Either authenticate with the GitHub CLI:

```bash
gh auth login
```

or provide a fine-grained user token through `GH_TOKEN`. The token needs
`Starring: write` and `Metadata: read`. `agent-thanks` never saves the token.

## HTTP 401: Bad credentials

The token is invalid, expired, revoked, or copied incorrectly. Replace the token
or renew the GitHub CLI login, then rerun `agent-thanks doctor`.

## HTTP 403: Forbidden

Common causes are:

- The token lacks `Starring: write`.
- An organization policy blocks the credential.
- A rate or abuse-prevention limit is active.
- `GITHUB_TOKEN` is an installation token from GitHub Actions rather than a
  user credential.

Use a fine-grained user token or a GitHub CLI user login. The CLI does not retry
mutating requests automatically.

## HTTP 404: Not Found

The repository may have been renamed, deleted, made unavailable to the active
account, or recorded incorrectly in the transcript. Inspect the candidate with:

```bash
agent-thanks review .agent-thanks-report.json
```

## No repository candidates found

Check the following:

1. The correct project was passed with `--repo`.
2. `--base` points to the state before the current work.
3. The dependency manifest is supported.
4. A transcript was supplied when the task reused code without adding a
   dependency.

For already committed work, `--base HEAD` produces no dependency diff. Use the
commit before the task, often `--base HEAD~1`.

## Unresolved dependency mappings

Some manifests contain package names rather than source repository URLs. The
tool queries PyPI, npm, and crates.io metadata to map those names. A package
stays unresolved when:

- `--offline` is enabled;
- the registry is unavailable;
- the package metadata has no GitHub source URL; or
- the ecosystem has no registry resolver yet.

Unresolved packages are never guessed and are never eligible for Star.

## A repository is shown as low confidence

A plain GitHub URL proves that a repository was mentioned, not that its work was
meaningfully reused. It remains visible in reports and optional Markdown export,
but it is not eligible for the Star flow. Passing it through `--repo` returns a
non-zero error rather than overriding its confidence.

Use an explicit provenance phrase such as `adapted from` only when it truthfully
describes the task. Do not edit transcripts merely to force a high-confidence
classification.

## Interactive terminal required

```text
Starring requires an interactive terminal; piped or unattended confirmation is
not accepted. Use --dry-run for automation.
```

Live Star and Unstar commands require terminal input. `yes | agent-thanks star`
and similar pipelines are rejected. Use `scan`, `review`, `export`, or
`--dry-run` in scripts and CI, then run `agent-thanks star` separately when a
person can inspect each repository.

The 0.3.x `config`, `auto`, `--mode`, `--yes`, and `--all --yes` paths no longer
exist. Old configuration files are ignored and do not need to be removed.

## The batch stopped partway through

Successful Stars are not hidden or rolled back automatically. The CLI prints an
Undo command containing the completed subset before returning a non-zero exit
code. Run that command if the batch should be reverted.

## The report contains a local path

Reports are local review artifacts and may include project or transcript paths.
`.agent-thanks-report.json` is ignored by this repository, but other projects
should add it to `.gitignore` before using the default report filename.
