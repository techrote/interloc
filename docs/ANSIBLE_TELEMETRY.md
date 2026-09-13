# Read-only Ansible telemetry (IL-018)

This adapter reads the user's separate trusted Ansible execution kernel. It is
not a Red Hat Ansible integration and does not execute the external kernel,
launch tasks, fetch updates, change another repository or obtain credentials.
It is a component API, not an already wired CLI command or background service.

## Compatibility and source authority

The reader is pinned to the inspected `ansible.execution.v1` per-job format at
`techrote/ansible` commit `ab1f9023545539f334ebc23635b54c8b03eac40d` (inspected
2026-09-13). The repository describes implementation 0.1.3 and a trusted-noop-only
profile. Interloc does not treat that profile as qualification for real agents.

Primary source references, read as text only:

| Source at the pinned commit | Git blob SHA | Reader significance |
|---|---|---|
| [Runtime contract](https://github.com/techrote/ansible/blob/ab1f9023545539f334ebc23635b54c8b03eac40d/docs/RUNTIME-CONTRACT.md) | `1b4cd1bbd9ce3c0c9e4c6a68d2b00c4db6999e56` | Outcome axes, terminal authority and evidence limits |
| [State implementation](https://github.com/techrote/ansible/blob/ab1f9023545539f334ebc23635b54c8b03eac40d/ansible_kernel/state.py) | `72a62d7819b5388937393496e10a7b5fdd3cdebb` | Hash-chain envelope and lifecycle |
| [Result schema](https://github.com/techrote/ansible/blob/ab1f9023545539f334ebc23635b54c8b03eac40d/schemas/result-v1.schema.json) | `5aa7836587c743ede38903e94bdcb343442b9002` | Exact result fields and enums |
| [Kernel metadata writer](https://github.com/techrote/ansible/blob/ab1f9023545539f334ebc23635b54c8b03eac40d/ansible_kernel/kernel.py) | `3f4e71877b1a983c1306ff8ce2244ef79a8be66a` | Actual metadata fields; slot/generation limitations |
| [Canonical encoding](https://github.com/techrote/ansible/blob/ab1f9023545539f334ebc23635b54c8b03eac40d/ansible_kernel/contract.py) | `318f89193bb157bda1ff871ac927750a8ef874a3` | ASCII-escaped JSON hashing, strict data parsing |

`interloc.ansible.status-chain.v1` names this adapter's profile, not a new producer
wire contract. Unknown fields/contracts, unsupported metadata, invalid hash
chains or lifecycle transitions fail closed. A producer source hash identifies
bytes, not semantic compatibility. Reassess compatibility against the new trusted
source before widening this profile; never execute it to repair a parse failure.

The producer's `metadata.json` does **not** contain slot or generation. A local
`Source` mapping can supply those as an operator assertion; the batch explicitly
labels their origin `operator_mapping`. Without them, both remain null and origin
is `unavailable`. The reader does not invent a ledger association, infer a slot
from directory order or claim a once-only generation was independently verified.
The job ID is the exact locally enrolled 32-hex run-directory basename.

## Local mapping and bounded reads

Construct `Source(alias, run_dir, job_id, slot=None, generation=None)` from trusted
local enrollment. The path must be absolute and its final directory must match
the job ID. It is never accepted from a remote request. A mapping reads only
`metadata.json`, `status.jsonl` and, after a terminal event, `result.json`.
Symlink/reparse redirection, hardlinks, FIFOs, devices and file replacement during
open are rejected. Missing files return `UNAVAILABLE` without creating directories.
No globbing, code imports, subprocesses or traversal of referenced paths occurs.

A read is capped at 256 KiB of status data, 64 KiB per record and 64 KiB for each
metadata/result file. JSON depth is at most 20 with 4,096 visited nodes; duplicate
keys, floats, nonfinite values and malformed Unicode are rejected. Accepted
implementation-version strings are bounded ASCII version labels matching the
pinned producer's current output. The profile deliberately rejects wider or
undocumented formats rather than guessing their meaning. A future producer
whose valid per-job data exceeds the bound needs an explicit compatibility change.

The entire bounded complete hash chain is checked on each poll. Its canonical
encoding uses the producer's ASCII escaping, not Interloc's UTF-8 canonical
request format. Sequences, previous digests, lifecycle edges, timestamps and
job-bound result shapes are validated. A partial final line is withheld until
complete and explicitly reported. Files too large to validate are not tail-read
as though an unverified suffix were an authoritative chain.

## Checkpoint and evidence handling

`TelemetryReader.poll()` returns a `TelemetryBatch` with a sanitized evidence
group and a proposed checkpoint. It does not advance the stored checkpoint.
Persist the evidence and checkpoint atomically, or retain evidence first and
accept repeated event IDs after interruption. Then call `accept_checkpoint`.
The caller owns durable checkpoint storage outside all source worktrees. The
adapter never writes to the producer's state directory.

Checkpoints bind the local source mapping, job, slot/generation, metadata hash,
file identity, complete-record byte offset, sequence and prefix hash. Restart
validates those bindings and the offset/sequence boundary. Metadata changes and
rewritten prefixes do not silently inherit a checkpoint. Rotation/truncation is
explicit and replays a newly validated prefix; stable job/hash-derived event IDs
allow downstream deduplication. A lower mapped generation is refused. Rebinding
a new job is an explicit local mapping change, not a task launch or authority grant.

Every content fragment goes through the existing `build_text_group` sanitizer
before exposure/export. Supply locally configured secrets and personal paths;
the source path is also registered for redaction. Structured batches expose only
bounded safe identity/status metadata, never raw result objects or paths.
Evidence output is capped at 256 KiB by default. `group.truncated` and
`dropped_sanitized_bytes` describe evidence-budget loss; `batch.truncation` is a
separate source-file event. Persist/report any evidence gap before accepting its
checkpoint. Chunk digests and generated paths use the existing evidence model.

The default stale threshold is 300 seconds. A stale valid record remains labelled
`STALE`, not live progress. A timestamp over 30 seconds into the future reports
`CLOCK_SKEW`. Age/clock failures never imply authority to run a recovery command.
Errors contain stable diagnostic codes, not raw exception strings or source data.

## Reported outcomes are not verified success

The terminal hash-chained status record is the source's result authority.
`result.json` is only a convenience copy; by itself it does not establish a
terminal event. If present after a terminal event it must match; a missing copy
is reported, not fabricated. Admission, infrastructure, transport, worker outcome
and evidence remain separate axes in the sanitized summary.

The adapter does not independently validate worker/provider manifests or success
predicates. `evidence_verified` is always false, including when the source reports
`worker_outcome=success` and `evidence=complete`. A hash chain checks internal
consistency, not authorship or truth. Same-account OS compromise is outside this
component's trust model. Actual orchestration and execution remain external.

## Verification

Run these commands separately from an installed checkout:

```powershell
python -m unittest discover -s tests/integrations -v
python -m unittest discover -s tests -v
python tools/audit_workflow.py
```

Synthetic fixtures match the pinned producer's documented envelope without
importing or executing producer code. Tests cover append/restart, checkpoint
commit ordering, rotation/truncation, stale generations, torn/hash-invalid data,
unknown schema, job mismatch, source-reported success, secret removal, bounded
output, missing installation, and unchanged source bytes. Hosted Windows tests
are filesystem/parser checks, not proof of access to the user's local Ansible
installation. Live local integration is **NOT RUN** until that source is separately
enrolled and available. No changes to `techrote/ansible` or `techrote/intrallm` are
part of this issue.
