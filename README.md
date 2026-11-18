# Agent Dashboard

A local dashboard that runs and orchestrates **Claude Code**, **Codex** and
**Antigravity** through their CLIs (`claude`, `codex`, `agy`). Define profiles and roles, create tasks, start runs, and watch each
session live.

- Runs on your machine at `http://127.0.0.1:8765`. No accounts, no telemetry, no API
  keys: AI work goes through the CLIs you installed and signed in to.
- Light by design: a Python server (Starlette, two dependencies) plus a Preact UI
  with no build step and no Node.js.

## Quick start (Windows PowerShell)

```powershell
cd dashboard
.\run.ps1 -Setup     # first time only: creates backend\.venv and installs dependencies
.\run.ps1 -Demo      # try it with a built-in fake CLI (nothing real runs)
.\run.ps1            # use the real claude / codex CLIs
```

Then open http://127.0.0.1:8765.

On Linux / macOS (needs Python 3.12+):

```bash
./run.sh --setup     # first time only
./run.sh --demo      # fake CLI
./run.sh             # real CLIs; --port 9000 to change the port
```

Without the script:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m app          # set DASH_FAKE_CLI=1 for demo mode
```

Settings via environment variables: `DASH_PORT` (default 8765), `DASH_DATA_DIR`
(default `%LOCALAPPDATA%\claude-codex-dash`), and `DASH_FAKE_CLI=1` for demo mode.

## Using it

1. **Environment**: check that `claude` / `codex` / `agy` are found. Install them
   and sign in yourself first. Run each once interactively, because headless runs
   reuse that sign-in.
   - For Antigravity, pre-approve the tools sessions need with `permissions.allow`
     rules in `~/.gemini/antigravity-cli/settings.json`, or use a full-access
     profile. Otherwise `agy` skips those tools without failing the run.
2. **Profiles**: create how each CLI is called (model, effort, permissions,
   timeout).
3. **Roles**: define what a session does (instructions + profile + working folder).
4. **Tasks**: create a task with a repository folder and acceptance criteria.
5. Open the task and choose **✦ Plan with AI**. Claude proposes a workflow: which
   agents, how many sessions, what each does and in which order. Send feedback until
   it's right, then **Approve & run**. At each approval point, read the output and
   approve it or send fix instructions.
   - For one session with one role, use **Run one role** instead: pick the role,
     check the preview (exact command and prompt), then Start.
6. Follow the run live in **Runs** and **Sessions**. Set how many sessions of each
   CLI may run at once in **Settings**.
7. **Tests** collects test results. Copy the snippet shown there into your Tester role:
   its sessions then end with a `submission` block, and each one becomes a row with
   pass/fail counts, TP/FP/TN/FN, precision/recall/F1 and the commit that was tested.

## Status

Phases 1–3 of [docs/features.md](./docs/features.md) are built: tasks, roles,
single-session runs, and AI-planned multi-agent workflows across Claude Code, Codex
and Antigravity. Next is Phase 4: submissions, so steps can report progress and ask
questions while they run.

## Development

See [AGENTS.md](./AGENTS.md) for architecture, conventions and the check commands.
