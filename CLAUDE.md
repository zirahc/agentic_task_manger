# CLAUDE.md

Contributor notes for Claude (and any coding agent). The full, binding version lives
in [AGENTS.md](./AGENTS.md); read it first. These are the points most often gotten
wrong:

1. **Build only what is specified** in [docs/features.md](./docs/features.md)
   (`[suggested]` items only once confirmed), in its Phases order. Ask before
   inventing behaviour. Use its vocabulary: Profile, Role, Team, Task, Run, Session,
   Submission, Rule.
2. **Stay light:** no build step, no Node, no npm packages, no new Python deps
   without approval. The frontend is Preact + htm ES modules. The backend is
   Starlette.
3. **Validate everything, weaken nothing:** `ruff format --check`, `ruff check`,
   `mypy app tests` (strict) and `pytest` in `backend/`. After UI changes, load the
   affected views in a browser and check the console.
4. **Boundaries:** the UI calls the server only through `frontend/src/lib/api.js`.
   Routes are thin and logic lives in `app/services/`. Only
   `app/runner/adapters.py` knows CLI flags. The agent CLIs (Claude Code, Codex,
   Antigravity `agy`) are listed once in `app/tools.py` and mirrored in
   `frontend/src/lib/util.js`.
5. **Processes:** argv only, no `shell=True`, prompt on stdin, timeouts, tree kill
   that works on Windows. Keep the `127.0.0.1` bind and the `Guard` checks (Host
   header + `X-Dash: 1`).
6. **No native dialogs:** use `confirmDialog()`, `Modal`, `Notice`, `CloseButton`
   and `toast()` from `components/ui.js`.
7. **AI-planned runs:** plans are untrusted until `plans.validate_plan` accepts them,
   nothing runs before owner approval, only `orchestrator.advance()` moves a
   planned run forward, and write steps never overlap.
8. **Antigravity skips denied tools without failing the run.** Keep its stderr permission
   notices visible as `warning` events, and never pass `-p` with stdin streaming.
9. **Tests for every behaviour change** (fake CLI + fixtures, never the real CLIs),
   plus `CHANGELOG.md` and `docs/features.md`.
10. `../orchevia/` is a reference for patterns and look & feel, not a code source.
