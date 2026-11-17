import { html, useState } from '../lib/html.js';
import { api, errorMessage } from '../lib/api.js';
import { navigate, useResource, duration } from '../lib/util.js';
import {
  Empty,
  Field,
  Loading,
  Modal,
  Notice,
  PageHeader,
  StatusPill,
  Time,
  confirmDialog,
  toast,
} from '../components/ui.js';
import { StartRunDialog } from './start-run.js';
import { PlanDialog } from './plan.js';
import { RunSessions } from './runs.js';

export const TASK_COLUMNS = [
  { id: 'backlog', label: 'Backlog' },
  { id: 'ready', label: 'Ready' },
  { id: 'running', label: 'Running' },
  { id: 'needs-you', label: 'Needs you' },
  { id: 'review', label: 'Review' },
  { id: 'done', label: 'Done' },
];

const EMPTY_TASK = {
  title: '',
  description: '',
  repoDir: '',
  branch: '',
  criteria: /** @type {string[]} */ ([]),
  priority: 'normal',
  status: 'backlog',
  labels: /** @type {string[]} */ ([]),
};

export function TasksView() {
  const { data, error, loading, reload, setData } = useResource(() => api.tasks(), [], 4000);
  const [editing, setEditing] = useState(/** @type {any} */ (null));
  const [dragOver, setDragOver] = useState(/** @type {string | null} */ (null));
  const tasks = /** @type {any[]} */ (data ?? []);

  const move = async (/** @type {string} */ id, /** @type {string} */ status) => {
    const task = tasks.find((t) => t.id === id);
    if (!task || task.status === status) return;
    setData(tasks.map((t) => (t.id === id ? { ...t, status } : t)));
    try {
      await api.moveTask(id, status);
    } catch (e) {
      toast(errorMessage(e), 'error');
    }
    void reload();
  };

  return html`<section class="view">
    <${PageHeader}
      title="Tasks"
      subtitle="Define the work, then start a run with the roles you choose."
      actions=${html`<button class="btn btn-primary" onClick=${() => setEditing(EMPTY_TASK)}>+ New task</button>`}
    />
    <div class="view-body view-body-board">
      ${error ? html`<${Notice} variant="error">${errorMessage(error)}<//>` : null}
      ${loading && !data
        ? html`<${Loading} />`
        : tasks.length === 0
          ? html`<${Empty}
              title="No tasks yet"
              action=${html`<button class="btn btn-primary" onClick=${() => setEditing(EMPTY_TASK)}>Create the first task</button>`}
            >
              A task describes what you want done: the goal, the repository and the acceptance criteria.
            <//>`
          : html`<div class="board">
              ${TASK_COLUMNS.map((col) => {
                const items = tasks.filter((t) => t.status === col.id);
                return html`<section
                  key=${col.id}
                  class=${`column ${dragOver === col.id ? 'drag-over' : ''}`}
                  aria-label=${col.label}
                  onDragOver=${(/** @type {DragEvent} */ e) => {
                    e.preventDefault();
                    setDragOver(col.id);
                  }}
                  onDragLeave=${() => setDragOver(null)}
                  onDrop=${(/** @type {DragEvent} */ e) => {
                    e.preventDefault();
                    setDragOver(null);
                    const id = e.dataTransfer?.getData('text/plain');
                    if (id) void move(id, col.id);
                  }}
                >
                  <header class="column-head">
                    <span class=${`column-dot dot-${col.id}`} aria-hidden="true"></span>
                    <h2>${col.label}</h2>
                    <span class="count">${items.length}</span>
                  </header>
                  <div class="column-body">
                    ${items.map((t) => html`<${TaskCard} key=${t.id} task=${t} />`)}
                  </div>
                </section>`;
              })}
            </div>`}
    </div>
    ${editing
      ? html`<${TaskForm}
          initial=${editing}
          onClose=${() => setEditing(null)}
          onSaved=${(/** @type {any} */ t) => {
            setEditing(null);
            void reload();
            if (!editing.id) navigate(`/tasks/${t.id}`);
          }}
        />`
      : null}
  </section>`;
}

/** @param {{task: any}} p */
function TaskCard({ task }) {
  return html`<a
    class=${`task-card priority-${task.priority}`}
    href=${`#/tasks/${encodeURIComponent(task.id)}`}
    draggable="true"
    onDragStart=${(/** @type {DragEvent} */ e) => e.dataTransfer?.setData('text/plain', task.id)}
  >
    <span class="task-title">${task.title}</span>
    <span class="task-meta">
      ${task.priority !== 'normal' ? html`<span class=${`prio prio-${task.priority}`}>${task.priority}</span>` : null}
      ${task.labels.map((/** @type {string} */ l) => html`<span class="label">${l}</span>`)}
      ${task.runCount ? html`<span class="muted small">${task.runCount} run${task.runCount === 1 ? '' : 's'}</span>` : null}
    </span>
  </a>`;
}

/** @param {{id: string}} p */
export function TaskDetail({ id }) {
  const task = useResource(() => api.task(id), [id], 4000);
  const runs = useResource(() => api.runs(id), [id], 3000);
  const [editing, setEditing] = useState(false);
  const [starting, setStarting] = useState(false);
  const [planning, setPlanning] = useState(false);
  const t = /** @type {any} */ (task.data);
  const openWorkflow = /** @type {any[]} */ (runs.data ?? []).find(
    (r) => r.mode === 'planned' && ['planning', 'running', 'awaiting-approval'].includes(r.status),
  );

  const remove = async () => {
    const ok = await confirmDialog({
      title: 'Delete task',
      message: `Delete "${t.title}" and all of its runs?`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteTask(id);
      toast('Task deleted');
      navigate('/tasks');
    } catch (e) {
      toast(errorMessage(e), 'error');
    }
  };

  const changeStatus = async (/** @type {Event} */ e) => {
    const status = /** @type {HTMLSelectElement} */ (e.currentTarget).value;
    try {
      task.setData(await api.moveTask(id, status));
    } catch (e2) {
      toast(errorMessage(e2), 'error');
    }
  };

  if (task.error) {
    return html`<section class="view">
      <${PageHeader} title="Task" actions=${html`<a class="btn" href="#/tasks">← Tasks</a>`} />
      <div class="view-body"><${Notice} variant="error">${errorMessage(task.error)}<//></div>
    </section>`;
  }
  if (!t) return html`<section class="view"><${Loading} /></section>`;

  return html`<section class="view">
    <${PageHeader}
      title=${t.title}
      actions=${html`
        <a class="btn" href="#/tasks">← Tasks</a>
        <button class="btn" onClick=${() => setEditing(true)}>Edit</button>
        <button class="btn btn-ghost danger-hover" onClick=${remove}>Delete</button>
        <button class="btn" onClick=${() => setStarting(true)} title="Run a single role on this task">▶ Run one role</button>
        <button class="btn btn-primary" onClick=${() => setPlanning(true)} title="Let AI design a multi-agent workflow">
          ✦ Plan with AI
        </button>
      `}
    />
    <div class="view-body detail-grid">
      <div class="detail-main">
        ${openWorkflow
          ? html`<${Notice} variant=${openWorkflow.status === 'awaiting-approval' ? 'warn' : 'info'}>
              ${openWorkflow.status === 'awaiting-approval'
                ? openWorkflow.stage === 'plan'
                  ? 'The AI workflow is waiting for you to review the plan.'
                  : 'The AI workflow is waiting for your approval.'
                : openWorkflow.status === 'planning'
                  ? 'The planner is designing a workflow.'
                  : 'An AI workflow is running.'}
              <a class="btn btn-sm" href=${`#/runs/${encodeURIComponent(openWorkflow.id)}`}>Open</a>
            <//>`
          : null}
        <section class="panel">
          <h2 class="panel-title">Description</h2>
          ${t.description ? html`<div class="prose">${t.description}</div>` : html`<p class="muted">No description.</p>`}
          ${t.criteria.length
            ? html`<h3 class="panel-subtitle">Acceptance criteria</h3>
                <ul class="checklist">
                  ${t.criteria.map((/** @type {string} */ c) => html`<li>${c}</li>`)}
                </ul>`
            : null}
        </section>
        <section class="panel">
          <h2 class="panel-title">Runs</h2>
          ${runs.data && runs.data.length === 0
            ? html`<p class="muted">
                No runs yet. <strong>Plan with AI</strong> lets the planner design a multi-agent workflow for your
                approval; <strong>Run one role</strong> runs a single session.
              </p>`
            : html`<${RunList} runs=${runs.data ?? []} />`}
        </section>
      </div>
      <aside class="detail-side panel">
        <dl class="meta">
          <dt>Status</dt>
          <dd>
            <select class="inline-select" value=${t.status} onChange=${changeStatus} aria-label="Status">
              ${TASK_COLUMNS.map((c) => html`<option value=${c.id}>${c.label}</option>`)}
            </select>
          </dd>
          <dt>Priority</dt>
          <dd><span class=${`prio prio-${t.priority}`}>${t.priority}</span></dd>
          <dt>Repository</dt>
          <dd class="mono break">${t.repoDir || html`<span class="warn-text">not set</span>`}</dd>
          ${t.branch ? html`<dt>Branch</dt><dd class="mono">${t.branch}</dd>` : null}
          ${t.labels.length
            ? html`<dt>Labels</dt><dd>${t.labels.map((/** @type {string} */ l) => html`<span class="label">${l}</span>`)}</dd>`
            : null}
          <dt>Created</dt>
          <dd><${Time} iso=${t.createdAt} /></dd>
          <dt>Updated</dt>
          <dd><${Time} iso=${t.updatedAt} /></dd>
        </dl>
      </aside>
    </div>
    ${editing
      ? html`<${TaskForm}
          initial=${t}
          onClose=${() => setEditing(false)}
          onSaved=${(/** @type {any} */ saved) => {
            setEditing(false);
            task.setData(saved);
          }}
        />`
      : null}
    ${starting
      ? html`<${StartRunDialog}
          task=${t}
          onClose=${() => setStarting(false)}
          onStarted=${(/** @type {any} */ run) => {
            setStarting(false);
            navigate(`/runs/${run.id}`);
          }}
        />`
      : null}
    ${planning ? html`<${PlanDialog} task=${t} onClose=${() => setPlanning(false)} />` : null}
  </section>`;
}

/** @param {{runs: any[]}} p */
export function RunList({ runs }) {
  return html`<div class="run-list">
    ${runs.map(
      (r) => html`<a class="run-row" key=${r.id} href=${`#/runs/${encodeURIComponent(r.id)}`}>
        <${StatusPill} status=${r.status} />
        <span class="run-roles"><${RunSessions} run=${r} /></span>
        <span class="muted small"><${Time} iso=${r.createdAt} /></span>
        <span class="muted small num">${duration(r.mode === 'planned' ? r.createdAt : r.sessions[0]?.startedAt, r.finishedAt)}</span>
      </a>`,
    )}
  </div>`;
}

/** @param {{initial: any, onClose: () => void, onSaved: (t: any) => void}} p */
function TaskForm({ initial, onClose, onSaved }) {
  const [form, setForm] = useState({
    ...initial,
    criteriaText: initial.criteria.join('\n'),
    labelsText: initial.labels.join(', '),
  });
  const [err, setErr] = useState(/** @type {any} */ (null));
  const [saving, setSaving] = useState(false);
  const set = (/** @type {string} */ key) => (/** @type {Event} */ e) =>
    setForm({ ...form, [key]: /** @type {HTMLInputElement} */ (e.currentTarget).value });
  const fieldErr = (/** @type {string} */ f) => (err?.field === f ? err.message : null);

  const submit = async (/** @type {Event} */ e) => {
    e.preventDefault();
    setSaving(true);
    setErr(null);
    const payload = {
      title: form.title,
      description: form.description,
      repoDir: form.repoDir,
      branch: form.branch,
      criteria: form.criteriaText.split('\n'),
      priority: form.priority,
      status: form.status,
      labels: form.labelsText.split(','),
    };
    try {
      const saved = initial.id ? await api.updateTask(initial.id, payload) : await api.createTask(payload);
      toast(initial.id ? 'Task saved' : 'Task created');
      onSaved(saved);
    } catch (e2) {
      setErr(e2);
    } finally {
      setSaving(false);
    }
  };

  return html`<${Modal}
    wide
    title=${initial.id ? 'Edit task' : 'New task'}
    onClose=${onClose}
    footer=${html`<button type="button" class="btn" onClick=${onClose}>Cancel</button>
      <button type="submit" form="task-form" class="btn btn-primary" disabled=${saving}>${saving ? 'Saving…' : 'Save'}</button>`}
  >
    <form id="task-form" class="form" onSubmit=${submit}>
      ${err && !err.field ? html`<${Notice} variant="error">${errorMessage(err)}<//>` : null}
      <${Field} label="Title" error=${fieldErr('title')}>
        <input value=${form.title} onInput=${set('title')} placeholder="Add rate limiting to the checkout API" required />
      <//>
      <${Field} label="Description" hint="What to do and any context the sessions need." error=${fieldErr('description')}>
        <textarea rows="7" value=${form.description} onInput=${set('description')}></textarea>
      <//>
      <${Field} label="Acceptance criteria" hint="One per line." error=${fieldErr('criteria')}>
        <textarea rows="3" value=${form.criteriaText} onInput=${set('criteriaText')}></textarea>
      <//>
      <div class="form-row">
        <${Field} label="Repository folder" hint="Where sessions work." error=${fieldErr('repoDir')}>
          <input value=${form.repoDir} onInput=${set('repoDir')} placeholder="C:\\work\\my-repo" />
        <//>
        <${Field} label="Branch" hint="Optional." error=${fieldErr('branch')}>
          <input value=${form.branch} onInput=${set('branch')} placeholder="feature/rate-limit" />
        <//>
      </div>
      <div class="form-row">
        <${Field} label="Priority">
          <select value=${form.priority} onChange=${set('priority')}>
            <option value="low">Low</option>
            <option value="normal">Normal</option>
            <option value="high">High</option>
            <option value="urgent">Urgent</option>
          </select>
        <//>
        <${Field} label="Status">
          <select value=${form.status} onChange=${set('status')}>
            ${TASK_COLUMNS.map((c) => html`<option value=${c.id}>${c.label}</option>`)}
          </select>
        <//>
        <${Field} label="Labels" hint="Comma separated." error=${fieldErr('labels')}>
          <input value=${form.labelsText} onInput=${set('labelsText')} placeholder="api, backend" />
        <//>
      </div>
    </form>
  <//>`;
}
