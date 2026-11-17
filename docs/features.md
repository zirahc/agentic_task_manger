# Features

The dashboard manages and orchestrates **Claude Code**, **Codex** and **Antigravity**
working through their CLIs (`claude`, `codex`, `agy`). You define what each session does, create tasks, choose how sessions work
on each task, and control what sessions report back while they run.

Each item is tagged:

- **[core]**: requested by the owner. In scope.
- **[suggested]**: proposed addition. In scope only after the owner confirms it.
  Remove the tag (or the item) when decided.

Build in phase order (see [Phases](#phases)). Ask before building anything not
described here.

---

## Glossary

| Term           | Meaning                                                                                                                  |
| -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **Profile**    | How to invoke one CLI: tool (`claude` / `codex` / `antigravity`), model, effort, permission level, timeout, executable path. |
| **Session**    | One running CLI conversation with a role, instructions and a submission contract. All three CLIs keep an id that can be resumed (Claude session id, Codex thread id, Antigravity `conversation_id`). |
| **Role**       | A reusable session definition: what the session does ("Planner", "Implementer", "Reviewer", "Tester"…).                 |
| **Team**       | A named set of roles with counts and an orchestration mode, e.g. "1 Claude planner → 2 Codex implementers → 1 Claude reviewer". |
| **Task**       | A unit of work you define: goal, context, repo, acceptance criteria.                                                     |
| **Run**        | One execution of a task by a team. A task can have many runs.                                                            |
| **Submission** | A structured report a session sends while running (plan, progress, question, result…).                                  |
| **Rule**       | "When submission X arrives from role Y → do Z": the mechanism that orchestrates the sessions.                            |

---

## 1. Sessions and roles [core]

The owner sets **how many** Claude, Codex and Antigravity sessions exist and **what
each one does**.

### 1.1 Profiles

- CRUD for profiles: tool, display name, model (free text), effort, permission level
  (CLI settings / allowed tools / full access), default timeout, optional
  executable path.
- Per-tool differences:
  - **Allowed tools** can be passed per run only for Claude Code. Codex has no
    allow-list, and Antigravity keeps its allow rules in its own `settings.json`.
  - Effort `max` is not offered for Codex.
  - **Antigravity soft-denies** tools that aren't pre-approved: the run still
    reports success. The dashboard shows those stderr notices as warnings in the
    session log, and explains this in the profile form and the run preview.
- **Check setup** button: detects the CLI, shows its version, and sends one tiny
  prompt to confirm it is signed in and answering. [suggested]

### 1.2 Roles (session definitions)

A role has:

- name, color/icon, description
- profile (which tool and model runs it)
- **instructions**: the standing prompt that defines its job (appended to every
  prompt the session receives)
- **submission contract**: which submissions it must or may send (see §3)
- working directory mode: task repo / isolated worktree / fixed folder
- limits: timeout, max turns, optional token/cost budget [suggested]

Built-in starter roles, editable [suggested]: Planner, Implementer, Reviewer,
Tester, Documenter.

### 1.3 Session capacity

- Global caps: **max concurrent sessions per CLI** (Claude, Codex, Antigravity),
  which respects subscription limits. Extra work waits in a queue.
- Per-run counts: how many sessions of each role a run starts (e.g. 3 implementers).
- **Sessions view**: every live session with role, tool, task, status
  (`queued / running / waiting / paused / succeeded / failed / cancelled`), elapsed
  time, last submission, and stop / pause / message actions.

---

## 2. Task management [core]

### 2.1 Tasks

- Fields: title, description (Markdown), repository folder, branch, acceptance
  criteria (checklist), priority, labels, attachments/links.
- **Board view** with columns `Backlog → Ready → Running → Needs you → Review → Done`,
  plus a list/table view with filters.
- Task detail page: description, run history, submissions timeline, changed files,
  usage.
- Task dependencies ("B starts after A is done"). [suggested]
- Import from a GitHub issue or Jira ticket. [suggested, later]

### 2.2 AI-planned workflows [core] ✅

The main way to run a task. Owner's example: Codex analyses `adblock.js` and writes a
report. Antigravity splits the adblock extensions into groups by detection method and
recommends approaches. Claude reviews both reports and writes the implementation plan.
The owner corrects the plan until it is right, then approves. Then code, tests and
review run, and the owner monitors it all per task.

1. **Plan with AI** (task page): a **planner** session reads the task and repository
   and proposes a **workflow**. Claude Code is the default planner, set in Settings;
   any profile can be chosen.
   - Inputs to the planner: the task, optional owner notes, every profile (id, tool,
     model, permissions), and editable **guidance** in Settings saying what each tool
     is good at.
   - Output: one JSON block with a `summary` and up to 16 `steps`.
2. **Steps:**
   - An `agent` step names a `profileId`, `access` (`read` | `write`), `inputs`
     (earlier step ids whose final reports it receives) and `instructions`.
   - A `gate` step pauses for the owner and `reviews` one agent step.
3. **Validation:** the plan is untrusted model output. It is checked against the real
   profiles: ids, inputs, no cycles, gate rules and size limits. An unusable answer
   is shown with its problems, and the owner can ask the planner to try again.
4. **Plan review loop:** the owner sees the workflow as stages, plus each step's
   instructions. They either **approve**, or send **feedback** to the planner, which
   returns a new version. All versions and feedback are kept.
5. **Execution:**
   - Steps start when their inputs succeed, and independent steps run in parallel
     within the per-tool limits.
   - **Write steps never overlap** within a run.
   - Each step's prompt contains its instructions, the task, the workflow summary,
     and the inputs' reports (each clipped at 40k characters).
6. **Gates:** the owner reads the reviewed step's output and then either
   **approves** it (its output flows to later steps) or sends **fix instructions**.
   The reviewed step then runs again with its previous output plus the feedback, and
   the gate asks again.
7. **Failure and control:**
   - A failed step stops new steps from starting.
   - The owner can **retry** a failed or cancelled step, or **cancel** the run.
   - Shutting down the dashboard marks running steps failed, so they can be retried.
8. **Monitoring:**
   - The task page shows a banner while a workflow is planning, running or waiting.
   - The run page shows the stage graph with live statuses.
   - Each step shows its output, live session log and attempts.
   - Run-wide token and CLI-reported cost totals are shown.
9. **Task status follows the workflow:**
   - planning or running → Running
   - waiting for the owner, or failed → Needs you
   - succeeded → Review
   - cancelled → Ready

10. **Parallel sessions for speed:** the owner asked for many sessions in parallel,
    for example one Codex session per extension to analyse and one per code part,
    then one Codex session that merges them.
    - **Fan-out analysis:** independent `read` steps run at the same time, up to
      each tool's session limit. The planner is told the limits, and the default
      guidance tells it to split work this way.
    - **Isolated coding:** an `access: isolated` step runs in its own git worktree,
      created from a snapshot of the task folder. The snapshot includes uncommitted
      and untracked files, and the user's index, branch and files are never touched.
    - Isolated steps run in parallel. When one finishes, its changes are saved as a
      patch file under the data folder and appended to its report (shown up to
      30k characters). The worktree is then removed.
    - **Merge:** a `write` step that lists the isolated steps as inputs is told to
      integrate their patches into the task folder (for example with `git apply`).
    - Isolated steps need the task folder to be in a git repository. The planner is
      told whether it is, and approval is refused otherwise.
    - Up to 30 steps per plan.

Not yet built:
- Editing the plan JSON by hand.
- Stopping a write step from overlapping with *another run* on the same repository.
- Cleaning up worktrees left behind by a crash (`git worktree prune` does it by hand).

### 2.3 Fixed modes (manual)

When the owner starts a task without the planner, a **Start run** dialog sets the
following. Today only **Single** (one role, one session) is built. The planner
already covers pipeline, parallel and review-loop shapes.

1. **Tools**: any mix of Claude, Codex and Antigravity.
2. **Team**: pick a saved team or build one ad hoc: which roles, how many sessions
   each, which profile per role.
3. **Orchestration mode** (how sessions work together):
   - **Single**: one session does the task.
   - **Pipeline**: roles run in order, each one receiving the previous one's result
     (Plan → Implement → Review).
   - **Parallel compare**: N sessions (e.g. Claude, Codex and Antigravity) solve the same task
     independently, then the owner (or a judge session) picks the best result.
   - **Lead and workers**: a lead session splits the task into subtasks (by
     submission), and the dashboard assigns them to worker sessions within capacity.
   - **Review loop**: implement → review → fix until there are no blocking findings,
     capped at N rounds.
4. **Gates**: where to stop for human approval (after plan, before commit, before
   PR). [suggested]
5. **Budget**: max wall time, max rounds, optional token/cost cap. [suggested]
6. A **preview** of the run graph and the exact commands before pressing Start.
   Nothing runs without an explicit Start.

Save the chosen setup as a **team preset** for reuse. [suggested]

---

## 3. Submissions: orchestrating what sessions report [core]

For each role, the owner defines **what the session must submit while running**.
Submissions turn free-form CLI output into structured events the dashboard can act on.

### 3.1 Submission types

Built-in types (each has a fixed JSON schema):

| Type       | Payload (summary)                                    | Typical use                          |
| ---------- | ---------------------------------------------------- | ------------------------------------ |
| `plan`     | steps[], risks[], files to touch                     | Planner before implementation        |
| `progress` | percent or step n/m, current activity, note          | Heartbeat while working              |
| `question` | question, options[], blocking (bool)                 | Session needs a human decision       |
| `artifact` | kind (patch, file, doc, test-report), path, summary  | Intermediate outputs                 |
| `findings` | items[] with severity P0–P3, file, line, message     | Reviewers                            |
| `subtasks` | items[] with title, description, suggested role      | Lead session in lead/workers mode    |
| `handoff`  | to role, message, context refs                       | Pass work to another role            |
| `result`   | status, summary, files changed, tests run, follow-ups | Required final submission           |
| `blocker`  | reason, what is needed                               | Session cannot continue              |

Custom submission types with an owner-defined JSON schema. [suggested]

### 3.2 Contract per role

For each role, the owner chooses which types are **required**, which are **allowed**,
and their frequency (e.g. `progress` at least every 10 minutes). A run whose session
ends without its required `result` is marked **incomplete**, not succeeded.

### 3.3 How sessions submit

1. **Preferred: a local MCP server** exposed by the backend with tools such as
   `submit_plan`, `submit_progress`, `ask_question`, `submit_result`… Each session is
   started with an MCP config pointing at it and a per-session token, so every
   submission is attributed to its session. Claude Code and Codex accept MCP
   config per run. Antigravity's docs show no per-run MCP flag (servers are managed
   with `/mcp` in its own settings), so check whether a one-time registration of the
   dashboard's server works headless. Until then, Antigravity uses the fallback.
2. **Fallback: fenced blocks** in the output (` ```submission {json} ``` `), parsed
   from the CLI's JSON event stream. Antigravity can also enforce a schema on the
   final answer with `--json-schema`, which suits the required `result` submission.

Submissions are validated against their schema. An invalid submission is stored with
an error and shown, never silently dropped.

### 3.4 Rules (orchestration logic)

Rules connect submissions to actions:

```text
WHEN <submission type> FROM <role> [WHERE <condition>] THEN <action>
```

Actions: start a session of role X with this payload · forward to a running session
(as its next turn) · pause for owner approval · notify · mark task Needs you / Review /
Done · stop the run.

Examples:

- `plan` from Planner → pause for approval → on approve, start Implementer.
- `findings` from Reviewer where any P0/P1 → send to Implementer (loop, max 3).
- `question` with blocking=true → task moves to **Needs you**, notify.
- `subtasks` from Lead → create one Implementer session per item, within capacity.

Each orchestration mode in §2.2 is a preset bundle of these rules. Advanced users can
edit rules directly. [suggested]

---

## 4. Live monitoring and control [suggested]

- **Run view**: graph or timeline of sessions with status, submissions in order, and
  a live readable activity log per session (current action, commands run, files
  changed). Raw output stays under a collapsible section.
- **Talk to a session**: send a message that is delivered as the session's next turn
  (headless CLIs cannot accept input mid-turn, so the dashboard resumes the session
  with `--resume` / `exec resume` once the current turn ends). Answering a
  `question` submission uses the same path.
- Pause (finish current turn, don't start the next), cancel (kill process tree),
  retry a failed session, recover a failed run from the failed step.
- **Inbox**: questions, approvals, failures and finished runs in one place, with a
  badge in the sidebar. Optional desktop notifications.

## 5. Safety and isolation [suggested]

- **Git worktree per session** when several sessions edit the same repo in parallel,
  so they don't overwrite each other. Merging back is an explicit step (owner or a
  merge role).
- Permission level visible on every role and run. Full-access runs are flagged.
- No secrets stored. Prompts and output can contain sensitive data, so the UI warns
  before export.

## 6. Usage and limits [suggested]

- Tokens (and USD where the CLI reports it) per session, run, task, role and model.
  CLI-reported numbers are kept separate from estimates.
- Detect usage-limit and rate-limit errors. Mark the session **waiting for limit**
  and resume or retry it later instead of failing the run.
- Daily chart and breakdown tables, as in the reference project's Usage view.

## 7. History and templates [suggested]

- Every run is kept: config snapshot, submissions, outputs, usage, and a read-only
  replay.
- Templates: save task + team + rules as a reusable template, and import/export as
  JSON.

## 8. Environment and settings [core basics, suggested extras]

- Environment view: detected `claude`, `codex`, `git`, `gh` with versions and paths.
  **Windows note:** npm-installed CLIs are `.cmd` shims, so the runner must resolve
  and launch them correctly without `shell=True`.
- Settings: concurrency caps, default permission level, theme, notification
  preferences, data folder location.

---

## Views (sidebar)

`Tasks` · `Runs` · `Sessions` · `Roles & Teams` · `Profiles` · `Inbox` · `Usage` ·
`Environment` · `Settings`

## Data model (initial sketch)

```text
profiles(id, tool, name, model, effort, permission, timeout_s, executable, …)
roles(id, name, profile_id, instructions, submission_contract JSON, workdir_mode, limits JSON)
teams(id, name, mode, members JSON[{role_id, count}], rules JSON)
tasks(id, title, description, repo_dir, branch, criteria JSON, priority, status, labels JSON, created_at, updated_at)
runs(id, task_id, team_snapshot JSON, status, started_at, finished_at, budget JSON)
sessions(id, run_id, role_id, tool, cli_session_id, status, workdir, started_at, finished_at, usage JSON)
submissions(id, session_id, run_id, type, payload JSON, valid, error, created_at)
events(id, run_id, session_id, kind, data JSON, created_at)   -- activity log + rule firings
inbox(id, run_id, kind, title, state, created_at)
```

Snapshots: a run stores a copy of the team, roles and rules it started with, so later
edits never change history.

## Phases

1. ✅ **Foundation**: scaffold frontend and backend, Environment view, Profiles,
   process runner (Windows-safe, timeouts, tree kill, stream parsing), single-session
   run of a task with a live log.
2. ✅ **Tasks and roles**: task board and detail, roles with instructions, global
   concurrency caps and queue, Sessions view. (Starter roles and profile
   validation are included. Check-setup probes, task dependencies and importing
   are not built yet.)
3. ✅ **AI-planned workflows** (§2.2). The owner moved this ahead of submissions: the
   planner, plan review with feedback versions, step graph execution with report
   hand-offs, gates with fix instructions, retry and cancel, and per-task monitoring.
4. **Submissions**: MCP submission server + fenced-block fallback, built-in types,
   role contracts, and a submissions timeline. These will let steps report progress
   and questions *during* a step. Today a step hands over only its final report.
5. **Orchestration extras**: saved teams, rules engine, Inbox, talk-to-session, and
   lead/workers (a step that creates more steps).
6. **Polish**: worktrees, usage view, limit handling, templates, history replay.

## Open questions for the owner

- Single user on this machine only, or should others on the network reach it (needs
  authentication)?
- Should sessions commit/push/open PRs, or stop at changes in the working tree?
- Maximum realistic concurrency (how many Claude / Codex / Antigravity sessions at once)?
- Which of the [suggested] items should be in scope?
