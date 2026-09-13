# IL-009 hosted verification receipt

Verified on 2026-09-13 for PR #44, `work/il-009-retention-20260913`.

| Input | Exact SHA | Workflow | Outcome |
|---|---|---|---|
| Implementation | `443998c004b9f6a151b7cc00799df93f85ed2dd3` | [34732645307](https://github.com/techrote/interloc/actions/runs/34732645307) | SUCCESS, six Windows/Ubuntu Python 3.12/3.13/3.14 jobs |
| Implementation plus runbook/evidence, PR validation | `110ea0b10825af9e3babcfa0c5a2eb6eed62ee4e` | [34732722103](https://github.com/techrote/interloc/actions/runs/34732722103) | SUCCESS |

The implementation tree is `a9a9430a59117ef81cb8de395d7a4a06df5161a3`.
The evidence/runbook tree is `d0ee41a5417a0773aeb20643b281ddb28473f67e`.
Both were compared against their locally tested/staged Git tree identities before
remote advancement. The full local suite is 151 tests, including 40 new retention
and recovery tests. Hosted jobs perform clean installation, full unit tests,
CLI smoke and the workflow audit under the unchanged repository CI definition.

This receipt changes documentation only. Its own PR-head checks must also finish
successfully before merge; their final status is retained in the PR/Actions
record rather than a self-referential commit claim. No hosted pass is represented
as a real desktop, live runtime-mailbox, power-cut or independent release test.

Deterministic IL-009 acceptance is satisfied by the verified implementation and
its documented component boundaries. IL-010 composition and IL-025/026 resource
and independent security qualification remain separate. IL-008 is still
unmerged and continues to block IL-010, regardless of IL-009's merge status.
