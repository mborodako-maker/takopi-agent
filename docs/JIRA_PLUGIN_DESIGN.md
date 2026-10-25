# Future takopi-jira command plugin (design only)

No Jira client, PAT, DB access or production integration is included now.
Target Jira version: 8.20.17; confirm its REST compatibility when implementing.

Use standard `takopi.command_backends` entry points for `/jira HEC-8576`,
`/jira_search <query>` and `/jira_work HEC-8576`. Reuse CommandContext,
CommandExecutor, RunRequest, project directives and standard worktree resolver.
Do not add a second project/session/worktree subsystem.

Flow: Telegram → Takopi → takopi-jira → jira-proxy → Jira 8.20.17.
The proxy alone stores the PAT, outside worker mounts/env/logs/model context.
Takopi authenticates to proxy with a narrowly scoped service identity, not the
Jira PAT. Codex gets only bounded, sanitized issue data, never raw headers or PAT.

Initial stage is read-only: allowlisted issue/project lookup and bounded JQL
search. Proxy enforces HTTP method/endpoint/project allowlists, rate/time/response
limits and auditing without credentials. Reject arbitrary URLs and raw proxying.
Issue text/attachments are untrusted prompt input, not instructions. Do not
forward sensitive attachments. Require the Telegram global sender check before
plugin dispatch and bind results to the requesting user/chat.

`jira_work` reads the issue, maps its project to an operator-configured Takopi
project, validates issue key and branch name, and creates a RunRequest with a
standard `RunContext(project=..., branch=...)`. Writes, transitions, comments,
production access and deploys require a separate later design/approval policy.
