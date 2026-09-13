# IL-012 native Windows capture matrix

Issue: #13. This procedure resolves G-capture only when executed on an actual interactive Windows desktop. Hosted CI, mocks, API documentation, or successful helper compilation are not substitutes.

## Preconditions

Use a disposable/synthetic desktop session. Close or hide private applications before testing. The source worktree must not receive capture output. Record:

- tested Interloc commit SHA;
- Windows edition/version/build and session type;
- CPU/GPU and display topology;
- Python version;
- browser/version for the WebGL fixture;
- Visual C++/Windows SDK version for WGC;
- backend and helper build hash;
- per-monitor scaling and target monitor coordinates.

Use only the fixtures in `experiments/capture/` and a Windows Terminal containing harmless synthetic text. Native samples remain local. Retain only SHA-256, dimensions, timings, outcome, and a reviewed textual description in repository evidence.

## Outcome vocabulary

- **PASS**: correct target content is present, dimensions are credible, target identity remained valid, and no unrelated desktop/window content appears.
- **FAIL**: backend returns an error, times out, produces a wrong/stale/blank frame, captures a different target, or violates the scoped-failure rule.
- **UNSUPPORTED**: documented/tested platform condition is intentionally not supported and fails closed without broader capture.
- **NOT RUN**: case was not executed. Never convert NOT RUN to PASS from documentation or expectation.

A successful API return alone is not PASS.

## Core matrix

Run both candidate backends unless a prerequisite is unavailable. `PrintWindow` is a comparator, not an automatic fallback for WGC.

| Case | Target | State | DPI / placement | WGC | PrintWindow | Required observation |
|---|---|---|---|---|---|---|
| C01 | Tk fixture | visible | 100%, primary | NOT RUN | NOT RUN | correct four-color geometry + current sequence marker |
| C02 | Tk fixture | fully occluded by another synthetic window | 100%, primary | NOT RUN | NOT RUN | correct target only; no occluder pixels |
| C03 | Tk fixture | minimized | 100%, primary | NOT RUN | NOT RUN | explicit result; blank/stale frame is not PASS |
| C04 | Tk fixture | visible | 150% | NOT RUN | NOT RUN | dimensions/content valid; no coordinate scaling mismatch |
| C05 | Tk fixture | visible | 200% | NOT RUN | NOT RUN | dimensions/content valid; no coordinate scaling mismatch |
| C06 | Tk fixture | visible | secondary monitor with negative X or Y coordinate | NOT RUN | NOT RUN | correct target; recorded negative coordinates preserved |
| C07 | Windows Terminal | visible | current DPI | NOT RUN | NOT RUN | only synthetic terminal content; no adjacent desktop |
| C08 | Windows Terminal | occluded | current DPI | NOT RUN | NOT RUN | target content semantics recorded honestly |
| C09 | Windows Terminal | minimized | current DPI | NOT RUN | NOT RUN | explicit result; no desktop fallback |
| C10 | WebGL fixture | visible | current DPI | NOT RUN | NOT RUN | both GPU triangles and fixture tag visible |
| C11 | WebGL fixture | occluded | current DPI | NOT RUN | NOT RUN | correct target only; no occluder pixels |
| C12 | WebGL fixture | minimized | current DPI | NOT RUN | NOT RUN | explicit result; blank/stale frame is not PASS |

If the test machine cannot provide 150/200% scaling or negative coordinates, mark those cases NOT RUN with topology reason; do not simulate them by editing metadata.

## Security/failure matrix

These cases verify fail-closed behavior rather than image quality.

| Case | Condition | Expected result |
|---|---|---|
| N01 | invalid/nonexistent HWND | reject; no image and no alternate target |
| N02 | valid HWND with wrong expected PID | reject before capture |
| N03 | close/recreate fixture after recording old HWND | reject stale handle/identity; never select replacement by title |
| N04 | exact-title comparator sees zero matches | reject |
| N05 | exact-title comparator sees multiple matches | reject |
| N06 | locked/noninteractive desktop | fail/unsupported; no monitor or desktop capture |
| N07 | inaccessible/elevated target from lower-integrity probe | fail/unsupported; no privilege escalation |
| N08 | protected/excluded-content target if a harmless test fixture is available | fail/blank/unsupported recorded honestly; no fallback |
| N09 | capture exceeds 10 s | terminate/timeout at supervising layer; no indefinite operator hang |
| N10 | target closes/changes PID during capture | discard result |

The experiment helper is not the production opaque-window-ID implementation. IL-013 still must bind handle + process creation identity + session/scope and revalidate it under broker policy.

## Per-case record

For every executed backend/case retain a local JSON/text record containing at least:

```text
case_id
backend
interloc_commit
helper_hash
windows_build
python_or_native_toolchain_version
target_kind
hwnd (local record only)
pid (local record only)
window_rect
dpi
state_label
output_width
output_height
output_sha256
output_bytes
wall_ms
peak_working_set_bytes (when measurable)
blank_or_stale_observation
content_validation
outcome = PASS|FAIL|UNSUPPORTED
notes
```

Do not commit real HWND/PID, screenshots, private titles, usernames, or absolute personal paths. The repository evidence should summarize only sanitized outcome data.

## Performance procedure

For each successful core case, run at least five fresh captures after one warm-up and retain median and maximum wall time. Record helper process peak working set where the OS exposes it. The project-level requirement remains bounded completion or typed timeout within 10 seconds; this matrix does not invent a tighter budget before measurement.

Measure helper startup + first-frame completion separately from any later PNG encoding/provider integration. A BMP prototype result is backend feasibility evidence, not the final IL-013 artifact format.

## Decision rule

G-capture can be resolved only after the matrix has enough real evidence to choose the smallest backend that:

1. targets only the enrolled window;
2. fails closed under invalid/inaccessible conditions;
3. handles the required Terminal and GPU fixture cases adequately for the supported matrix;
4. has acceptable packaging/consent consequences; and
5. never needs a monitor/desktop fallback.

If neither backend meets those requirements, IL-012 completes with a negative backend decision and IL-013 remains blocked until a new scoped candidate is evaluated.