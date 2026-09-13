# Read-only project and worktree adapters (IL-016)

This is an opt-in component API, not a new CLI command or a complete courier.
The existing CLI still exposes help/doctor only. Runtime composition, durable
request scheduling and operator approval UI remain separate tasks; this change
neither includes nor replaces the blocked IL-008 implementation.

## Capability and authority contract

| Capability | Required arguments | Optional arguments / limits |
|---|---|---|
| `github.issue.read` | `repo_alias`, `number` | Number 1..2147483647; rejects pull requests |
| `github.issues.list` | `repo_alias` | `state`: open/closed/all (open); `page`: 1..1000 (1); `per_page`: 1..100 (20) |
| `github.pr.read` | `repo_alias`, `number` | Number 1..2147483647; reports head SHA, never checks out |
| `github.checks.read` | `repo_alias`, `commit_sha` | Full lowercase 40-character SHA; page/per_page as above |
| `git.worktree.status` | `worktree_alias` | No requester-provided paths or command options |

Aliases are ASCII letters/digits/underscore/hyphen, 1..64 characters, starting
with a letter or digit. Unknown fields, endpoints, hostnames, headers, shell
strings, executable paths, environment variables and arbitrary arguments are
rejected. The existing request envelope, byte limits, canonical digest and expiry
validation apply. Recognizing an extension in the schema does not enable it.

`ReadAdapters` is disabled by default and does not self-register. A local
integrator must supply both the normal mailbox `Enrollment` and separate
`Project`/`Worktree` mappings. A project grant has scope `project:<repo_alias>`;
a worktree grant has scope `worktree:<worktree_alias>`. No mailbox credential or
mailbox-only grant automatically enrolls a project. `Project.capabilities` is an
additional exact allowlist. A confirm grant uses the existing `Approval` model,
bound to request digest, policy revision, expiry and the adapter's target-state
fingerprint. Requester labels are never authority.

Call `observe(request, origin=trusted_origin, approval=local_approval)` only from
the locally trusted composition layer. The origin is transport metadata, not a
field copied from requester text. The result is `ReadResult(code, evidence)`;
`evidence` is a bounded sanitized `EvidenceGroup`. Neither `observe` nor any
import performs publication. The caller must still use the broker journal,
retention and approved transport before any export. Do not wire this directly to
a mailbox polling loop while its prerequisite control path remains unmerged.

## GitHub reads

`GhReadApi` uses a locally selected absolute native executable, fixed github.com
host, fixed GET endpoint shapes and fixed API headers. It does not expose gh's
field/input-file, placeholder, arbitrary endpoint, extension or pagination
features. Authentication uses the existing authorized gh login in the broker
context; token environment variables and debugging/injection variables are not
forwarded, read or printed. Helpers receive no broker home/auth context.

Each observation performs at most three successful GETs: repository identity,
one object or explicit page, then repository identity again. Numeric repository
ID, exact full name and expected visibility must match before and after. Returned
object numbers/IDs/URLs and check-run commit associations are validated. A rename,
visibility change, wrong object, expiry, cancellation or local policy change
causes the data to be discarded rather than exported as success. Checks are
reported check-run records, **not** an aggregate pass, branch-protection result
or merge authorization. A full list page reports `possibly_more`; it does not
claim to have fetched the complete repository. Pull requests are explicitly
excluded from issue-list results and counted as skipped.

The gh API object keeps an in-memory 120-attempt/hour limit and server backoff.
Reuse that object in a long-lived broker; constructing a new object resets the
local attempt history, so this is not a durable cross-process rate limiter.
Retry-After and exhausted rate budgets delay subsequent requests. No automatic
retry, credential repair or permission expansion is attempted. Common codes are
`AUTH_UNAVAILABLE`, `PERMISSION_DENIED`, `NOT_FOUND_OR_HIDDEN`, `RATE_LIMITED`,
`NOT_MODIFIED`, `REMOTE_UNAVAILABLE` and `REMOTE_IDENTITY_CHANGED`. Private/hidden
404 responses are not mislabeled as proof the object does not exist.

## Native process limits

The process helper concurrently drains both pipes in 4096-byte blocks through a
four-slot queue. A gh call allows at most 1 MiB stdout and 16 KiB stderr; a Git
status call allows 256 KiB stdout. Overflow rejects the observation while the
process is running, not after an unbounded capture. Calls have a 15-second native
timeout, cancellation checks and request-expiry checks. The worktree metadata
scan has a 30-second overall deadline; cleanup has separately bounded waits.
These are buffer/lifetime limits, not a claim of a hard total native-process RAM
ceiling or the independent IL-025 performance qualification.

On POSIX the helper creates a process group and terminates descendants on exit.
On Windows it creates the child suspended, assigns it to a kill-on-close Job
Object, and only then resumes its initial thread. Failure to establish containment
returns `CONTAINMENT_UNAVAILABLE`; there is no uncontained fallback. Batch/cmd
executables, relative executable names, inherited stdin and shell evaluation are
not accepted. The Windows implementation is qualified by the CPython matrix,
not a general claim about other Python implementations or every Windows build.

## Worktree safety and support envelope

`Worktree` requires three independently enrolled absolute local paths: working
root, per-worktree Git directory and common Git directory. A linked-worktree
`.git` pointer and `commondir` must identify exactly those enrolled directories.
Directory identity is established from filesystem stat identifiers after path
and link checks, so Windows long/8.3 aliases can represent the same enrolled
directory without treating a different directory as enrolled.
They are not authority to automatically enroll another location. UNC/device/ADS
paths, symlinks, junctions and special files are refused.

The adapter reads bounded HEAD/ref/index metadata and creates a disposable Git
directory with copied HEAD/index, an exact head ref and its own minimal config.
It reads objects from the enrolled object store, but never loads the target's
config/includes, credential helpers, fsmonitor, filter commands, custom diff
commands or hooks. Global/system configuration and executable injection
variables are disabled. Status uses porcelain v1 NUL output, no optional locks,
no renames and no submodule recursion. No reset, checkout, stash, index refresh
or target-worktree repair is performed. Tests compare all synthetic repository
file hashes before and after the supported read.

The result explicitly says `configuration: isolated-no-filters` and
`submodules: not_inspected`. **This need not match the user's ordinary configured
Git status.** Clean/smudge filters, EOL normalization, custom excludes and other
personal configuration are deliberately not inherited. A conservative dirty
observation must not be used to decide a reset, overwrite or commit operation.

Supported test fixtures include SHA-1 repositories, loose/packed refs, ordinary
and linked worktrees, detached/unborn HEAD, staged/deleted/modified/untracked
files and spaced/Unicode paths. Current limits: index 16 MiB, packed refs 2 MiB,
20,000 scanned entries and 256 MiB per working-tree/object-store inventory,
1,000 status entries. Unsupported cases fail explicitly: external object
alternates, reftable, SHA-256 format, split index, non-ASCII branch-ref parsing,
links/special files, oversized repositories or unavailable native Git. Do not
change the user's repository to fit this envelope.

HEAD/index/path identity and a deterministic filesystem-stat inventory are
rechecked after observation. Detected changes discard results. This is not an
atomic filesystem snapshot or protection against an OS-account adversary who
can race or replace installed software; that remains outside SECURITY's threat
model. It does not continuously monitor other agents' worktrees.

## Privacy and reproduction

Only selected fields reach the evidence boundary. String values are sanitized
before JSON escaping so escaping cannot conceal a configured secret or personal
path; the existing streaming sanitizer/chunker supplies the final second pass,
byte/hash descriptors and explicit truncation metadata. Native stderr and raw
API bodies are not returned to callers. Regex-based sanitization still cannot
guarantee detection of every secret. Use synthetic data for qualification and
review enrollment before enabling real project observations.

Run these commands separately from a clean installed checkout:

```text
python -m unittest discover -s tests/capabilities -v
python -m unittest discover -s tests -v
python tools/audit_workflow.py
interloc --help
interloc doctor --json
```

The first command includes actual disposable local Git/process tests and fake
GitHub API tests. A real gh read of a separately enrolled disposable project is
a distinct live check; source-repository connector reads are not a substitute.
See [IL-016 evidence](evidence/IL-016.md) for exact SHAs, CI and limitations.
