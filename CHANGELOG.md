# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Test results (first slice of Phase 4 submissions):**
  - Sessions can end their answer with a fenced ```` ```submission ```` JSON block. The
    dashboard stores each block when the session ends (new `submissions` table), with
    the commit that was checked out (`-dirty` when files differed).
  - Built-in type `test-result`: status, suite, summary, pass/fail/skip counts,
    TP/FP/TN/FN and per-case rows. Missing counts are derived from the cases.
    Precision, recall, F1, accuracy and false-positive rate are computed by the
    dashboard, not the model.
  - Invalid blocks are stored with the reason and their raw text, never dropped.
  - New **Tests** view: results table with a task filter, per-case details (failures
    first), delete, and a copyable instruction snippet for the Tester role.
  - API: `GET /api/submissions?type=&taskId=`, `DELETE /api/submissions/{id}`.
  - `./run.sh` launcher for Linux/macOS.

- **Parallel sessions in AI workflows:**
  - New step access `isolated`. Several coding sessions run at once, each in its own
    git worktree created from a snapshot of the task folder (uncommitted and
    untracked files included, the user's repository untouched).
  - Each part's changes are saved as a patch and passed to a merge `write` step
    that integrates them.
  - The planner now knows the per-tool session limits and whether the folder is a
    git repository, and its default guidance tells it to fan out analysis and coding
    across parallel sessions.
  - Plans may have up to 30 steps.
  - The workflow graph shows "N in parallel" per stage and "isolated copy" badges,
    and steps show their patch file.
- **AI-planned workflows** (task page → **✦ Plan with AI**):
  - A planner session (Claude Code by default) proposes a workflow of agent steps
    (tool/profile, read or write access, inputs, instructions) and owner gates.
  - The plan is validated against the real profiles. Unusable answers are shown with
    their problems.
  - Plan review: approve, or send feedback for a new version. Every version and piece
    of feedback is kept.
  - Execution as a dependency graph: each step receives the reports of its inputs,
    independent steps run in parallel, and write steps never overlap.
  - Gates show the reviewed output. Approve it, or send fix instructions that make
    the step revise its output and ask again.
  - Retry failed steps or cancel the run.
  - Task status follows the workflow.
  - The run page shows a stage graph with live status, per-step output and logs, and
    token and cost totals.
  - Settings → AI planner: the default planner profile, and editable guidance on
    what each tool is good at.
- Shutdown now waits for running sessions to record their outcome. They are marked
  failed with a "retry the step" message instead of cancelled.
- **Antigravity (`agy`) as a third agent CLI**, alongside Claude Code and Codex:
  - Profiles, roles, runs, its own concurrency limit, and detection in Environment.
  - Runs use stdin streaming (`--input-format stream-json --output-format
    stream-json`), so the prompt never goes on argv.
  - Its `init` / `step_update` / `result` events are parsed, with text deltas joined
    into messages, and thinking tokens are shown.
  - Full access maps to `--dangerously-skip-permissions`.
  - Stderr permission notices (agy soft-denies tools but still reports success) are
    shown as warnings in the session log.
- Effort levels `xhigh` and `max` (`max` is not offered for Codex).
- Project scaffold: a Starlette backend (SQLite, migrations, request guard) and a
  no-build Preact + htm frontend served by the backend.
- **Profiles**: how to invoke `claude` / `codex` (model, effort, permission level,
  allowed tools, timeout, executable override), with validation.
- **Roles**: session definitions with instructions, profile, working-folder mode,
  timeout override and color, plus starter roles (Planner, Implementer, Reviewer).
- **Tasks**: a board with drag-and-drop between Backlog / Ready / Running / Needs you /
  Review / Done, and a task detail page with criteria and run history.
- **Runs (single mode)**: a start dialog with a preview of the exact command and
  prompt, and runs that keep a snapshot of their role and profile.
- **Supervisor**: per-tool concurrency limits with a queue, timeouts, process-tree
  cancel (Windows-safe), and failing of sessions interrupted by a restart.
- **Live session log**: parsed Claude `stream-json` and Codex `exec --json` events
  (messages, commands with exit codes, file changes, tools, errors, result), plus
  CLI-reported token usage and cost.
- **Sessions view** with capacity meters. **Environment** view that detects
  `claude`, `codex`, `git` and `gh`. **Settings** for concurrency and theme.
- Windows npm `.cmd` shim resolution to `node script.js`, and a plain-arguments-only
  cmd.exe fallback.
- Demo mode (`DASH_FAKE_CLI=1`) with a bundled fake CLI.
