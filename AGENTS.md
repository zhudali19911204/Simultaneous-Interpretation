# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `src/simultaneous_interpreter/`. `app.py` owns the Tkinter UI and orchestration; `qwen_backend.py` handles realtime WebSocket sessions; `audio_devices.py` manages WASAPI devices; `meeting_minutes.py`, `provider_config.py`, and `settings_store.py` contain service and configuration logic. The entry point is `src/main.py`.

Tests are in `tests/` and mirror module names (`test_qwen_backend.py`, `test_ui_theme.py`). `scripts/` contains manual diagnostics. Packaging inputs live in `packaging/`, `TeamsInterpreter.spec`, and `build_release.ps1`. Generated `build/`, `dist/`, and `release/` directories are ignored and must not be committed.

## Build, Test, and Development Commands

Run commands from the repository root in Windows PowerShell:

```powershell
.\setup.ps1                             # create .venv and install runtime dependencies
.\run.ps1                               # start the desktop application
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe .\scripts\audio_smoke_test.py
.\build_release.ps1                     # create the Windows x64 release ZIP
```

Install `requirements-build.txt` before packaging if PyInstaller is unavailable.

## Coding Style & Naming Conventions

Use Python 3.12, four-space indentation, type annotations, and `from __future__ import annotations`. Follow `snake_case` for functions and variables, `PascalCase` for classes, and uppercase names for constants. Prefer small, testable helpers and frozen dataclasses for immutable records. Keep UI colors and ttk styles centralized in `ui_theme.py`; do not introduce hard-coded widget colors. No formatter is configured, so keep changes PEP 8-compatible and run `git diff --check`.

## Testing Guidelines

Tests use the standard-library `unittest` framework. Name files `test_<module>.py` and methods `test_<behavior>`. Add pure logic tests for parsing, validation, settings, and event handling. Mock network and audio boundaries; do not require live API credentials in automated tests. For UI or audio changes, document a manual Windows smoke test in the PR.

## Commit & Pull Request Guidelines

Recent commits use concise Conventional Commit subjects such as `feat: redesign desktop UI`. Use `feat:`, `fix:`, `docs:`, `test:`, or `build:` and keep each commit focused. PRs should summarize user-visible behavior, list verification commands, link relevant issues, and include screenshots for UI changes. Call out changes affecting Teams routing, credentials, or provider compatibility.

## Security & Configuration

Never commit API keys, WorkspaceIds, `settings.json`, credentials, logs, or meeting content. Credentials belong in Windows Credential Manager; non-secret settings remain under the current user’s application-data directory. Release archives must not contain local configuration.
