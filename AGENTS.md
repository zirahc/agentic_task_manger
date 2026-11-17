# AGENTS.md — instructions for coding agents (and humans) working in this repo

This is a local dashboard web app that orchestrates locally installed AI coding CLIs:
Claude Code (`claude`), Codex (`codex`) and Antigravity (`agy`). A small Python server on this machine runs the CLIs. A
no-build Preact UI in the browser manages them.

The UX and engineering conventions are adapted from the reference project in
`../orchevia/` (a Tauri desktop app). Use it as a **reference for patterns and look &
feel**, not as code to import: its Rust/Tauri layers do not exist here.

## Stack (deliberately light)

| Layer    | Choice                                                                         |
| -------- | ------------------------------------------------------------------------------ |
| Frontend | Preact + htm as plain ES modules, **no build step, no Node.js**                 |
|          | (vendored in `frontend/vendor/preact-htm.js`), JSDoc types, one CSS file       |
| Backend  | Python 3.12+, Starlette + Uvicorn (only two runtime deps), stdlib `sqlite3`     |
| Storage  | SQLite (WAL, foreign keys on), append-only migrations in `app/db.py`           |
| Tests    | pytest + Starlette `TestClient`, with a bundled fake CLI                       |
| Quality  | ruff (format + lint) and mypy `--strict`                                       |
| Platform | Windows first-class; macOS/Linux must keep working                             |

Do not add a frontend build tool, framework, or npm dependency. Do not add backend
dependencies without the owner's approval. If a library is truly needed in the
frontend, vendor a pinned ES-module build into `frontend/vendor/`.

## Layout

```text
dashboard/
  frontend/
    index.html, styles.css
    vendor/preact-htm.js       Preact + hooks + htm (pinned: htm@3.1.1 standalone)
    src/main.js                shell, sidebar, hash routing
    src/lib/api.js             the ONLY module that calls the server
    src/lib/util.js            time formatting, readLocal/writeLocal, routing, useResource
    src/components/ui.js       Modal, Notice, CloseButton, StatusPill, Field, toast, confirmDialog
    src/views/*.js             one module per view
  backend/
    app/main.py                app factory, request guard, static files
    app/api/routes.py          thin handlers: parse → service → JSON
    app/services/              domain logic (the authority): profiles, roles, tasks, runs, supervisor
    app/runner/                CLI resolution, argv building, stream parsing, process control
    app/db.py, validate.py, errors.py, config.py
    tests/                     pytest suite + real CLI stream fixtures
  docs/features.md             the binding feature spec
```

## Commands (run all before finishing)

```powershell
cd backend
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy app tests
.venv\Scripts\python.exe -m pytest -q
```

Run the app: `.\run.ps1` (real CLIs) or `.\run.ps1 -Demo` (fake CLIs), then open
http://127.0.0.1:8765. Fix failures instead of weakening rules (no blanket
`# type: ignore`, `noqa`, skipped tests or loosened config).

There is no JS toolchain. After a frontend change, load each affected view in a
browser (headless Chrome screenshots work) and check the console for errors.

## Architecture (do not blur these lines)

- **The frontend never runs processes or touches the database.** All server access
  goes through `frontend/src/lib/api.js`.
- **Routers are thin.** `app/api/routes.py` reads the body, calls `app/services/`,
  and returns JSON. Business rules live in services and are testable without HTTP.
- **The backend is authoritative** for validation. Field errors come back with a
  `field` so forms can show them inline.
- **Only `app/runner/adapters.py` knows CLI flags.** Services and UI never build argv.
- **The tool list lives in `app/tools.py`**, mirrored by `TOOLS` in
  `frontend/src/lib/util.js`. To add a CLI: add it in both places, then add an adapter
  branch, a parser branch, a fake-CLI mode and tests.
- **CLI specifics:**

  | Tool        | Headless call                                        | Prompt on stdin               | Full access flag                             | Allow-list                  |
  | ----------- | ---------------------------------------------------- | ----------------------------- | -------------------------------------------- | --------------------------- |
  | Claude Code | `claude -p --output-format stream-json --verbose`    | raw text                      | `--permission-mode bypassPermissions`        | `--allowedTools a,b`        |
  | Codex       | `codex exec --json … -`                              | raw text                      | `--dangerously-bypass-approvals-and-sandbox` | none                        |
  | Antigravity | `agy --input-format stream-json --output-format stream-json` | one `{"event":"user","message":{"content":…}}` line, then EOF | `--dangerously-skip-permissions` | only in agy's `settings.json` |

  Antigravity soft-denies tools it may not use: the run exits 0 with `SUCCESS` and
  only a stderr notice reports it. `parse.stderr_event` promotes such lines to
  visible `warning` events. Never drop them into hidden stderr. Never pass `-p` to
  `agy` in stdin mode, because a flag prompt is ignored there. The `agy` stream
  format comes from the official docs (antigravity.google/docs/cli/headless). The
  test fixture is built from those docs, so capture a real stream when `agy` is
  available and replace it.
- **Process execution** (`app/runner/`): argv lists only, never `shell=True`. Prompts
  go on **stdin**, never argv. Every process has a timeout. Cancel/timeout kills the
  whole tree (Windows: `CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F`; POSIX: process
  group). Partial output is kept. npm `.cmd` shims are resolved to `node script.js`.
  The cmd.exe fallback accepts only plain arguments.
- **The supervisor** (`services/supervisor.py`) owns the queue and the per-tool
  concurrency limits. It starts sessions on worker threads and records events to
  SQLite. Request handlers never block on a CLI.
- **Restart safety:** sessions still `running` at startup are marked failed. Their
  processes are gone, and silently resuming external work is unsafe. Shutdown waits
  for workers to record their outcome before closing the DB.
- **AI-planned runs** (`mode = 'planned'`):
  - `services/plans.py` owns the plan format, validation and every prompt.
    `services/orchestrator.py` owns the state machine.
  - `orchestrator.advance()` is the only code that moves a planned run forward. It
    runs in one DB transaction and is called after every session change
    (`orchestrator.refresh`) and every owner action.
  - A plan is untrusted model output. Never run a step that `validate_plan` has not
    accepted against the current profiles. Nothing executes before the owner
    approves.
  - Never let two `write` steps of one run be active at once.
  - `isolated` steps may run in parallel because each one gets its own worktree
    (`app/runner/git.py`): a snapshot commit made through a temporary index, then
    `git worktree add --detach`, then a diff saved as a patch, then the worktree is
    removed. Never touch the user's index, branch or working files from that code.
    Write patches as bytes, because Windows text mode corrupts them.
- **Local-only:** the server binds `127.0.0.1`. The `Guard` middleware rejects
  foreign `Host` headers (DNS rebinding) and API calls without `X-Dash: 1`
  (cross-site requests). Never relax either. No accounts, no telemetry, no API keys.

## API contract

- JSON is `camelCase` on the wire, and DB columns are `snake_case`. Each service has
  an explicit `serialize()`. Update it and the JS consumer together.
- Errors use one shape: `{ "code": "kebab-case", "message": "human text", "field"? }`
  with a proper HTTP status. The UI turns rejections into text via `errorMessage()`.
- A run stores a snapshot of the role and profile it started with, so later edits
  never change history.
- Schema changes add a new migration; never edit one that shipped.

## Python rules

- Full type hints, `mypy --strict` clean. Validate request bodies with `validate.Body`.
- No bare `except`. Catch broad exceptions only at loop/worker boundaries, and log
  them. No `assert` outside tests.
- `pathlib` for paths. Never assume POSIX-only behaviour.

## UI conventions

- Layout: a collapsible left sidebar with one entry per view. Each page has a header
  (title, one-line subtitle, actions on the right), with content below.
- **No native dialogs:** never `window.confirm` / `prompt` / `alert`. Use
  `confirmDialog()`, `<Modal>`, `<Notice>`, `<CloseButton>` and `toast()` from
  `components/ui.js`. Don't hand-roll close buttons, banners or modal shells.
- Dark-first CSS variables in `styles.css`. The light theme is
  `[data-theme='light']` overrides only.
- Status pills use one shared set: `status-queued/running/succeeded/failed/paused/cancelled`.
- Every view has loading, empty and error states. Destructive actions always confirm.
  Icon-only buttons get an `aria-label`.
- Long text: capped scroll regions, `<details>`, ellipsis + `title`. Never an
  unbounded wall of output.
- Timestamps: `<Time iso>` / `formatTime` (the exact value goes in `title`).
  `localStorage` only through `readLocal` / `writeLocal`.
- Polling: only what is live (an active run or session log, the sidebar's
  live-session count). Stop polling when a run ends.
- htm drops whitespace that contains a line break, so write `${' '}` where a space
  between inline elements matters.
- Usage numbers: keep CLI-reported values apart from estimates, and label which is which.

## Tests

Every behaviour change comes with tests in `backend/tests/`. Use a temp-dir SQLite,
the fake CLI (`app/runner/fake_cli.py`, which recognises the `FAKE_FAIL` / `FAKE_SLOW`
/ `FAKE_DENY` prompt keywords for all three tools), and stream fixtures for parsers.
Tests never call the real `claude` / `codex` / `agy`.

## Docs to update with behaviour changes

`CHANGELOG.md` (Keep a Changelog format), `docs/features.md` (mark phases/items as
done or changed), and this file when conventions change.

## Features

The full spec lives in [docs/features.md](./docs/features.md) and is binding. Summary:

1. **Sessions & roles**: profiles (how to call `claude` / `codex`), roles (what a
   session does: instructions, submission contract, workdir, limits), global
   concurrency caps per tool with a queue, and a live Sessions view.
2. **Task management**: a task board and task detail.
   - **Plan with AI:** a planner session proposes a workflow of agent and gate
     steps. The owner revises it through feedback rounds, then approves it.
     `services/orchestrator.py` runs it as a DAG: report hand-offs, write steps
     serialized, gates with fix instructions, retry and cancel.
   - **Run one role:** the manual single-session path.
3. **Submissions**: sessions report structured events (`plan`, `progress`,
   `question`, `findings`, `subtasks`, `handoff`, `result`, …) through a local MCP
   server, with a fenced-block fallback. **Rules** (`WHEN submission FROM role THEN
   action`) drive the orchestration.

Items tagged **[suggested]** in the spec are not in scope until the owner confirms
them. Follow the spec's **Phases** order. The domain vocabulary (Profile, Role, Team,
Task, Run, Session, Submission, Rule) is fixed: use these names in code, API and UI.
