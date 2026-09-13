# IL-012 capture feasibility harness

This directory contains the native/manual experiment harness for IL-012 / issue #13. It is not the production `capture.window` provider and it adds no runtime dependency to Interloc.

## Boundaries

The harness targets one explicit application window. It does not implement monitor capture, desktop fallback, active-window guessing, input automation, or publication. Native samples remain local and are not committed to the source repository.

A successful API return is not sufficient evidence. Each retained case must record the intended target, dimensions, output hash, observed state, runtime, and whether the resulting image actually contains the expected synthetic content. Blank, stale, or wrong-window frames are failures or unsupported outcomes.

## Candidate backends

### Windows Graphics Capture

`wgc_probe.cpp` is a narrow C++/WinRT snapshot prototype. It creates a `GraphicsCaptureItem` from an explicit HWND with `IGraphicsCaptureItemInterop::CreateForWindow`, starts a D3D11 capture session, waits for one frame with a bounded timeout, revalidates the target process, and writes a local BMP.

Microsoft documents `CreateForWindow` as targeting a single window and requiring Windows 10 version 1903 (build 18362) or newer. WGC is therefore the primary candidate, not yet the selected backend. Backend selection requires the real matrix in `tests/manual/capture-matrix.md`.

The prototype build requires Visual C++ Build Tools and a current Windows SDK with C++/WinRT headers. That toolchain is an experiment/build consequence, not a runtime dependency decision. If WGC is selected, IL-013 and IL-024 must define how the helper is packaged without requiring a developer toolchain on the operator machine.

### PrintWindow comparator

`printwindow_probe.py` is a standard-library ctypes comparator using `PrintWindow`. Microsoft documents `PrintWindow` as synchronous and as having the owning application render into the supplied device context. Its behavior can differ for GPU/composited, minimized, protected, or elevated windows, so it is not assumed to be a safe fallback.

The comparator accepts an explicit HWND or an exact title that must resolve uniquely, optionally checks the expected PID, captures only the requested window/client area, and writes a BMP. It never substitutes desktop capture.

## Fixtures

`fixture_window.py` provides a deterministic Tk window with high-contrast geometry. `gpu_fixture.html` provides a local WebGL2 fixture with deterministic geometric/color regions and no network resources. Windows Terminal is the third required real target and should contain only harmless synthetic fixture text.

## Required native matrix

The complete procedure is in `tests/manual/capture-matrix.md`: synthetic fixture, Windows Terminal, and GPU/WebGL fixture; visible, occluded, and minimized states; representative DPI settings; negative-coordinate monitor placement; locked/access-denied/elevated/protected cases; runtime/memory observations; and wrong-window/handle-reuse rejection.

Repository work can prepare this harness and evidence template without an interactive Windows desktop. It cannot resolve G-capture or close issue #13 until that native matrix is actually executed and retained. Hosted Windows CI is useful for source compatibility but is not the interactive capture qualification.