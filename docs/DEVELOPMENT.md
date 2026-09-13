# Development setup

Interlocuator targets Python 3.12+ and keeps the IL-001 foundation free of third-party runtime/test dependencies. Build dependencies are exactly pinned in `pyproject.toml`; `requirements-dev.lock` documents that there are no additional development packages yet.

## Windows PowerShell

Run each command below **separately**, pressing Enter after each line:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\interloc.exe --help
.\.venv\Scripts\interloc.exe doctor --json
.\.venv\Scripts\python.exe tools\audit_workflow.py
```

The `doctor` command is intentionally read-only: it reports the local runtime-root path and optional native-provider status without creating directories, authenticating, installing software, or importing absent native providers.

## Linux development check

Run these commands separately:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/interloc doctor --json
.venv/bin/python tools/audit_workflow.py
```

Runtime state belongs outside the repository. By default Windows uses `%LOCALAPPDATA%\\Interlocuator`; set `INTERLOC_HOME` explicitly for an alternate absolute local path. Source-tree runtime/cache names remain ignored by `.gitignore` as a secondary defense.
