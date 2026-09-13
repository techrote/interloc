# Interim Greenshot capture provider (IL-013)

Interloc uses an already-installed Greenshot instance as the **interim** screenshot backend. This component is deliberately small: it validates an enrolled foreground window, confirms `Greenshot.exe` is running in the same interactive Windows session, emits only Greenshot's configured window-capture hotkey, observes one new PNG in a dedicated local autosave directory, validates/hashes the bytes, and copies them into Interloc's local artifact directory.

It is not a general keyboard automation API, a desktop-capture API, a Greenshot plugin, or a Chat/image-delivery mechanism. The WGC/PrintWindow experiment remains available for later deeper integration but does not block this provider.

## Required local Greenshot configuration

The provider reads the user's Greenshot INI file; it does not edit it. Configure these values through Greenshot's normal preferences or another deliberate local procedure:

- **Window capture:** non-interactive (`CaptureWindowsInteractive=false`).
- **Window hotkey:** a PrintScreen-based chord. The normal default is `Alt + PrintScreen`; Ctrl/Alt/Shift modifiers are allowed, but no letter/function/Windows-key chord is accepted.
- **Destination:** direct default-file output only (`FileDefault`). Picker/editor/clipboard/printer/mail destinations must not be combined with the Interloc path.
- **Format:** PNG.
- **Output directory:** a dedicated local directory explicitly enrolled in Interloc.
- **Copy saved path to clipboard:** disabled. Interloc never needs to read the clipboard and capture must not overwrite it as a side effect.

The exact INI path is local configuration rather than a remote argument. Interloc does not scan arbitrary user directories to find configuration files.

If these settings do not match, capture fails with `GREENSHOT_CONFIG_MISMATCH`. Interloc never rewrites Greenshot preferences automatically.

## Target and consent boundary

`CaptureScope` is created locally with an opaque scope UUID and explicit allowed process IDs. `list_windows()` enumerates only visible top-level windows belonging to those processes and returns short-lived opaque `window_id` leases. Each lease binds HWND, PID and process-creation time.

Before, during and after capture, the provider verifies that the same HWND/PID/creation identity remains enrolled. The target must already be the foreground window. Interloc never activates, raises or focuses it. A request cannot supply an HWND, PID, title pattern, key sequence or output path.

Runtime integration must still apply the central Interloc policy/approval flow to `window.list` and `capture.window`. This component does not self-register and does not replace the unmerged IL-008 local-control implementation.

## Capture sequence

1. Verify provider enabled, scope/window lease valid, target identity unchanged, and target already foreground.
2. Re-read and validate Greenshot configuration.
3. Snapshot the bounded dedicated PNG output directory.
4. Verify a process named `Greenshot.exe` exists in the current Windows session.
5. Emit exactly the validated Greenshot window hotkey using the internal Windows `SendInput` helper.
6. Wait at most 10 seconds for exactly one new/changed regular PNG. Multiple outputs, links/reparse points, directory overflow or timeout fail closed.
7. Revalidate the target and foreground state.
8. Read the stable file under the configured 16 MiB ceiling and validate PNG chunk CRCs, IHDR dimensions (maximum 4096 x 4096), IDAT presence and terminal IEND.
9. Revalidate the target again, SHA-256 the exact bytes, and atomically copy them under a generated opaque ID in the local artifact directory.

The original Greenshot autosave remains untouched. The returned artifact is `delivery_state="local_only"`; successful capture does not imply remote availability or model vision.

Greenshot's current hotkey implementation uses a global keyboard hook and marks matching events handled before invoking its capture handler. The same-session process check reduces the risk that a missing Greenshot instance turns the configured PrintScreen chord into ordinary OS behavior. It is not a cryptographic handshake: Greenshot could theoretically exit between the process check and `SendInput`. That small race is an explicit limitation of this quick interim bridge and is a reason a deeper direct invocation may still be preferable later.

## Failure semantics

Important stable errors include:

| Code | Meaning / operator action |
|---|---|
| `PROVIDER_DISABLED` | Provider not locally enabled. |
| `SCOPE_DENIED` / `WINDOW_NOT_ENROLLED` | Use an explicitly enrolled scope/window lease. |
| `WINDOW_ID_EXPIRED` | Refresh the window inventory and approve a fresh target. |
| `WINDOW_IDENTITY_CHANGED` | The HWND/process identity changed; do not retry under the old approval. |
| `WINDOW_NOT_FOREGROUND` | Bring the already-approved target to the foreground manually; Interloc will not focus it. |
| `GREENSHOT_CONFIG_UNAVAILABLE` | Supply the correct local Greenshot INI path. |
| `GREENSHOT_CONFIG_MISMATCH` | Adjust Greenshot through its normal local settings to the bounded configuration above. |
| `GREENSHOT_HOTKEY_UNSUPPORTED` | Use a PrintScreen-based window hotkey without collisions. |
| `GREENSHOT_NOT_RUNNING` | Start Greenshot in the same interactive Windows session. |
| `GREENSHOT_PRESENCE_CHECK_FAILED` | Process/session verification could not be completed; no key is sent. |
| `HOTKEY_INJECTION_FAILED` | Windows refused the bounded hotkey input; no broader input fallback is attempted. |
| `OUTPUT_TIMEOUT` | Verify Greenshot auto-save is working; no desktop fallback occurs. |
| `OUTPUT_AMBIGUOUS` | More than one PNG changed during the capture window; use a dedicated/quieter output directory and retry with fresh consent as appropriate. |
| `OUTPUT_UNSAFE` | The changed output is not a normal local file. |
| `IMAGE_INVALID` / `IMAGE_TOO_LARGE` / `IMAGE_DIMENSIONS_UNSUPPORTED` | The produced file failed the bounded PNG contract. |

## Privacy and retention

Raw captures stay local. Neither the Greenshot source file nor the Interloc artifact copy belongs in the source repository. The provider does not OCR, redact or certify image content; local preview/review is still required before any later attachment/export. Image delivery to ordinary Chat remains the separate IL-014/IL-015 path.

Greenshot is an environmental prerequisite for this interim adapter. Interloc does not install, update, bundle or modify Greenshot, and does not use its external-command plugin or expose a generic Greenshot command line.
