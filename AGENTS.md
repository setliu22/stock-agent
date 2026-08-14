# Repository Instructions

## GitHub And Network Diagnostics

- Treat failures from `gh`, `git`, package registries, and other networked tools
  under default sandbox permissions as potentially caused by restricted network
  access.
- If `gh auth status` reports an invalid token in the sandbox, rerun the same
  read-only check with network escalation before diagnosing expired credentials
  or asking the user to authenticate again.
- Do not recommend GitHub CLI login, refresh, or logout commands based only on
  a sandboxed authentication check.
- Ask the user to reauthenticate only when the escalated check also reports an
  authentication failure.
- Apply the same escalation-first diagnostic rule when an important GitHub or
  dependency command fails with DNS, connection, or other likely network-access
  errors.
