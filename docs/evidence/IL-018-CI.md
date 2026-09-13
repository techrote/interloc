# IL-018 hosted verification and merge receipt

Verified 2026-09-13 for PR #45. This is a historical receipt for the exact checked
head, not a claim that a later documentation commit has already passed.

| Item | Verified value |
|---|---|
| Implementation SHA | `4ca5c1e72f71d34792ff510ead1acae68ab481d5` |
| Exact tested implementation tree | `7ad997122c68f1d1fda427a89c68bdb1937ea3b9` |
| Final PR head, including runbook/evidence | `58ceef60b1c7651b5ee7bead8b77c5c4e8749c47` |
| Final PR tree | `07240d97dfb9ecabc84167a35d2d71f04e9488cc` |
| PR CI | [34733379747](https://github.com/techrote/interloc/actions/runs/34733379747), SUCCESS before merge |
| Merge | [PR #45](https://github.com/techrote/interloc/pull/45), `ba27ecf3ddf7d1e56bc4db8e9e63227fe70a14c5` |
| Canonical issue | [#26](https://github.com/techrote/interloc/issues/26), closed/completed after verified merge |

All six hosted jobs completed successfully: Windows and Ubuntu, each on Python
3.12, 3.13 and 3.14. Each job ran installation, unit tests, CLI smoke and the
planning workflow audit. The jobs and merge state were independently fetched
before recording this receipt. The merged local suite is 183 tests, including
32 new telemetry tests; exact tree identity matched before branch advancement.

This verifies deterministic parser/filesystem/evidence integration, not the
availability of the user's live Ansible installation. Live read-only smoke is
NOT RUN; issue #26 explicitly permits synthetic fixtures when no enrolled source
is available. No source-repository read is represented as a runtime smoke.
Reported worker success remains unverified; optional reference fetching is absent.

The producer source, schema limits, operator-mapped slot/generation, checkpoint
ordering and unavailable capabilities remain documented in
[ANSIBLE_TELEMETRY](../ANSIBLE_TELEMETRY.md) and [IL-018](IL-018.md). IL-019 still
requires unmerged IL-008; neither issue closure nor this receipt bypasses it.
