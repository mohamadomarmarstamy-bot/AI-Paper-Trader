# Automatic paper code repairs

Enable JARVIS_PAPER_REPAIR_ENABLED=true on the existing production service. The GitHub workflow jarvis-paper-repair.yml polls at minutes 7, 22, 37 and 52; scheduled Actions may be delayed. No laptop, new credential, private key or additional hosting service is required. The repository is public and uses standard GitHub-hosted runners. The existing $25 investigator AI reservation budget is unchanged.

## What is automatic

An investigator may submit an exact-source, single-function replacement with a plain unittest regression. Automatic scope is entry_quote_budget and next_exit in jarvis_test_bot.py, and chart_fresh_for in jarvis_research.py. They govern paper scheduling. Other proposals stay needs_review. No change can edit live trading, accounting, spending or risk constants, authentication, promotion criteria, the runner, its policy, GitHub workflows, or existing tests.

The shared policy validates both in the server and independently on the trusted runner. Helpers keep their signatures, cannot import or perform I/O, access private attributes, dynamically call code, mutate inputs, rebind trusted names or recurse. Iteration is bounded to comprehensions with at most two nested generators. Return values for paper quote budgets and exit selection also have host-side bounds/fallbacks outside generated scope.

## Proof and deployment

1. Only a proposal tied to an unresolved actual incident, current source SHA-256, and a valid regression is claimed. One active repair at a time, at most two starts per rolling 24 hours. Claims bind to the workflow run and an opaque, expiring lease. Stale proposals are not silently rebased.
2. The runner checks out trusted main. It copies backend code without .git, environment files or credentials. Candidate and regression run only in an unprivileged Docker container with no external network, read-only filesystems, dropped capabilities, bounded memory/CPU/processes, a temporary scratch area, and test timeouts.
3. The regression must run, fail by assertion on the baseline, and pass on the replacement. Errors, skipped tests and expected failures are rejected. The complete existing test_solana*.py suite must also pass, using unchanged trusted tests. Model tests are never installed in production.
4. Recheck main has not advanced. Commit exactly the one approved helper file and make a normal fast-forward push; never force. Existing Railway source deployment handles the commit.
5. Require the reported deployed commit to match and three independent healthy samples, at least 20 seconds apart. The server separately checks actual commit, worker age and overdue checks before accepting a deployed result. This does not automatically mark the original incident resolved; its existing two-minute recovery confirmation still applies.
6. If health cannot be verified, revert only the runner's own commit, push normally, and verify the rollback. If main advanced concurrently, do not overwrite another change; alert for review. A rolled-back fix is not reported as successful. Expired or failed runners retain their proposal and escalate rather than claiming recovery.

## Runner identity

GitHub's RS256 OIDC signature is verified using its fixed public JWKS endpoint, with strict issuer, custom audience, immutable repository ID, repository name, main ref, exact workflow path, GitHub-hosted runner, accepted event and short time window. PR and feature-branch tokens are rejected. Owner session cookies cannot claim or report repairs. No GitHub write token enters the app or model, and no app credential enters the untrusted test container. The workflow's short-lived contents-write credential remains with the trusted controller.

The investigator UI shows runner heartbeat, scope and repair history. Private lease/token hashes are excluded. Logs and reports contain fixed outcomes and checks, not credentials or provider bodies.

## Testing and rollback limits

Local: 887 Solana tests passed, including policy escape rejection, exact-source enforcement, regression grammar, cryptographic identity verification, run-bound leases, test evidence, independent deployed-health verification and expiration handling. A separate PR workflow performs an actual Docker rehearsal using a deliberately broken fixture and a restoring patch without touching production. The production runner must also prove OIDC access after merge.

These gates reduce risk; they do not prove arbitrary code is correct or guarantee trading profit. A whole-platform outage cannot be fixed inside the app. The isolated runner can roll back its own deployment when GitHub, the deployment platform and its coordinator remain reachable. It never removes another person's concurrent work. The generated-code scope stays limited until additional areas have equivalent isolation and tests.
