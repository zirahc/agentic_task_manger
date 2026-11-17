// AI-planned runs: the planner proposes a workflow, the owner reviews it (with feedback
// rounds), approves it, then watches and steers the steps.
import { html, useEffect, useMemo, useState } from '../lib/html.js';
import { api, errorMessage } from '../lib/api.js';
import { duration, isActive, isRunActive, navigate, toolInfo, useResource } from '../lib/util.js';
import { Field, Loading, Modal, Notice, StatusPill, Time, ToolBadge, confirmDialog, toast } from '../components/ui.js';
import { SessionLog } from './runs.js';

// ---- start dialog ---------------------------------------------------------------------

/** @param {{task: any, onClose: () => void}} p */
export function PlanDialog({ task, onClose }) {
  const profiles = useResource(() => api.profiles(), []);
  const settings = useResource(() => api.settings(), []);
  const [profileId, setProfileId] = useState('');
  const [notes, setNotes] = useState('');
  const [err, setErr] = useState(/** @type {unknown} */ (null));
  const [busy, setBusy] = useState(false);
  const list = /** @type {any[]} */ (profiles.data ?? []);

  useEffect(() => {
    if (profileId || !profiles.data || !settings.data) return;
    const configured = /** @type {any} */ (settings.data).planner?.profileId;
    const claude = list.find((p) => p.tool === 'claude');
    setProfileId((list.find((p) => p.id === configured) ?? claude ?? list[0])?.id ?? '');
  }, [profiles.data, settings.data]);

  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      const run = await api.planTask(task.id, { profileId, notes });
      onClose();
      navigate(`/runs/${run.id}`);
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  return html`<${Modal}
    title=${`Plan with AI · ${task.title}`}
    onClose=${onClose}
    footer=${html`<button type="button" class="btn" onClick=${onClose}>Cancel</button>
      <button type="button" class="btn btn-primary" disabled=${busy || !profileId} onClick=${start}>
        ${busy ? 'Starting…' : '✦ Start planning'}
      </button>`}
  >
    ${!profiles.data
      ? html`<${Loading} />`
      : list.length === 0
        ? html`<${Notice} variant="info">
            Create agent profiles first: the planner chooses among them.
            <button class="btn btn-sm" onClick=${() => navigate('/profiles')}>Go to Profiles</button>
          <//>`
        : html`<div class="form">
            <p class="muted">
              The planner reads the task and the repository and proposes a workflow: which agents to use, how many
              sessions, what each one does and in which order. Nothing else runs until you approve it.
            </p>
            ${err ? html`<${Notice} variant="error">${errorMessage(err)}<//>` : null}
            <${Field} label="Planner" hint="Claude Code is recommended. The default is set in Settings.">
              <select value=${profileId} onChange=${(/** @type {Event} */ e) => setProfileId(/** @type {HTMLSelectElement} */ (e.currentTarget).value)}>
                ${list.map((p) => html`<option value=${p.id}>${p.name} (${toolInfo(p.tool).label})</option>`)}
              </select>
            <//>
            <${Field} label="Notes for the planner" hint="Optional: constraints, preferences, what to avoid.">
              <textarea rows="4" value=${notes} onInput=${(/** @type {Event} */ e) => setNotes(/** @type {HTMLTextAreaElement} */ (e.currentTarget).value)}></textarea>
            <//>
            <p class="muted small">The planner can only use the profiles you have: ${list.length} available.</p>
          </div>`}
  <//>`;
}

// ---- run view -------------------------------------------------------------------------

/** @param {{run: any, onChange: (run: any) => void}} p */
export function PlannedRun({ run, onChange }) {
  const act = async (/** @type {() => Promise<any>} */ fn, /** @type {string} */ done) => {
    try {
      onChange(await fn());
      if (done) toast(done);
    } catch (e) {
      toast(errorMessage(e), 'error');
    }
  };
  return run.stage === 'plan'
    ? html`<${PlanReview} run=${run} act=${act} />`
    : html`<${Execution} run=${run} act=${act} />`;
}

const STAGE_TEXT = /** @type {Record<string, string>} */ ({
  planning: 'The planner is designing the workflow…',
  'awaiting-approval': 'Review the proposed workflow. Approve it, or tell the planner what to change.',
  cancelled: 'This plan was cancelled.',
});

/** @param {{run: any, act: (fn: () => Promise<any>, done: string) => Promise<void>}} p */
function PlanReview({ run, act }) {
  const planners = run.sessions.filter((/** @type {any} */ s) => s.purpose === 'planner');
  const latest = planners[planners.length - 1];
  const waiting = run.status === 'awaiting-approval';
  return html`
    <${Notice} variant=${waiting ? 'warn' : 'info'}>${STAGE_TEXT[run.status] ?? ''}<//>
    ${run.error ? html`<${Notice} variant="error">${run.error}<//>` : null}
    ${run.plan
      ? html`<section class="panel">
          <header class="panel-row">
            <h2 class="panel-title">Proposed workflow</h2>
            <span class="pill status-pending">version ${run.planVersion}</span>
            ${run.status === 'planning' ? html`<span class="muted small">revising…</span>` : null}
          </header>
          <p class="plan-summary">${run.plan.summary}</p>
          <${PlanGraph} plan=${run.plan} />
          <${StepList} plan=${run.plan} />
        </section>`
      : null}
    ${waiting
      ? html`<section class="panel decision">
          <${FeedbackBox}
            label="Ask the planner to change something"
            placeholder="e.g. Use Antigravity to compare detection methods per browser, and add a step that writes unit tests."
            submitLabel="Send to planner"
            onSend=${(/** @type {string} */ text) => act(() => api.sendFeedback(run.id, 'plan', text), 'Sent to the planner')}
          />
          <div class="decision-actions">
            <button
              class="btn btn-primary"
              disabled=${!run.plan}
              onClick=${async () => {
                const ok = await confirmDialog({
                  title: 'Approve workflow',
                  message: `Start the ${String(run.plan.steps.length)} steps of version ${String(run.planVersion)}? Sessions run with their profiles' permissions.`,
                  confirmLabel: 'Approve & run',
                });
                if (ok) await act(() => api.approvePlan(run.id), 'Workflow started');
              }}
            >
              ✓ Approve & run
            </button>
          </div>
        </section>`
      : null}
    ${latest
      ? html`<section class="panel">
          <details open=${isActive(latest.status) || !run.plan}>
            <summary>
              Planner session${planners.length > 1 ? ` · round ${String(planners.length)}` : ''} ·
              ${toolInfo(latest.tool).label}${latest.model ? ` (${latest.model})` : ''}
            </summary>
            ${latest.error ? html`<${Notice} variant="error">${latest.error}<//>` : null}
            <${SessionLog} key=${latest.id} sessionId=${latest.id} active=${isActive(latest.status)} />
          </details>
        </section>`
      : null}
    <${History} run=${run} />
  `;
}

/** @param {{run: any, act: (fn: () => Promise<any>, done: string) => Promise<void>}} p */
function Execution({ run, act }) {
  const steps = /** @type {any[]} */ (run.plan?.steps ?? []);
  const states = run.steps ?? {};
  const gate = steps.find((s) => s.kind === 'gate' && states[s.id]?.status === 'awaiting-approval');
  const [selected, setSelected] = useState(/** @type {string | null} */ (null));
  const auto =
    gate?.id ??
    steps.find((s) => isActive(states[s.id]?.status))?.id ??
    steps.find((s) => states[s.id]?.status === 'failed')?.id ??
    [...steps].reverse().find((s) => states[s.id]?.status === 'succeeded')?.id ??
    steps[0]?.id;
  const current = steps.find((s) => s.id === (selected ?? auto));
  const done = steps.filter((s) => states[s.id]?.status === 'succeeded').length;

  return html`
    ${run.error ? html`<${Notice} variant="error">${run.error}<//>` : null}
    <section class="panel">
      <header class="panel-row">
        <h2 class="panel-title">Workflow</h2>
        <span class="pill status-pending">version ${run.planVersion}</span>
        <span class="muted small">${done} / ${steps.length} steps done</span>
      </header>
      <p class="plan-summary">${run.plan?.summary}</p>
      <${PlanGraph} plan=${run.plan} states=${states} selected=${current?.id} onSelect=${setSelected} />
    </section>
    ${gate ? html`<${GatePanel} run=${run} gate=${gate} act=${act} />` : null}
    ${current && current.id !== gate?.id ? html`<${StepDetail} key=${current.id} run=${run} step=${current} act=${act} />` : null}
    <${History} run=${run} />
  `;
}

/** @param {{run: any, gate: any, act: (fn: () => Promise<any>, done: string) => Promise<void>}} p */
function GatePanel({ run, gate, act }) {
  const reviewed = run.plan.steps.find((/** @type {any} */ s) => s.id === gate.reviews);
  const output = run.steps[gate.reviews]?.output ?? '';
  return html`<section class="panel decision gate-panel">
    <header class="panel-row">
      <span class="gate-icon" aria-hidden="true">✋</span>
      <h2 class="panel-title">${gate.title}</h2>
      <${StatusPill} status="awaiting-approval" />
    </header>
    ${gate.instructions ? html`<p class="muted">${gate.instructions}</p>` : null}
    <h3 class="panel-subtitle">
      Output of “${reviewed?.title}” ${reviewed ? html`<${ToolBadge} tool=${reviewed.tool} />` : null}
    </h3>
    <div class="prose result-text tall">${output || html`<span class="muted">No output.</span>`}</div>
    <${FeedbackBox}
      label=${`Fix instructions for “${reviewed?.title ?? gate.reviews}”`}
      placeholder="What should change? The step revises its output and this review comes back to you."
      submitLabel="Send fix instructions"
      onSend=${(/** @type {string} */ text) => act(() => api.sendFeedback(run.id, gate.id, text), 'Sent for revision')}
    />
    <div class="decision-actions">
      <button class="btn btn-primary" onClick=${() => act(() => api.approveStep(run.id, gate.id), 'Approved, continuing')}>
        ✓ Approve and continue
      </button>
    </div>
  </section>`;
}

/** @param {{run: any, step: any, act: (fn: () => Promise<any>, done: string) => Promise<void>}} p */
function StepDetail({ run, step, act }) {
  const state = run.steps?.[step.id] ?? { status: 'pending' };
  const sessions = run.sessions.filter((/** @type {any} */ s) => s.stepId === step.id);
  const latest = sessions[sessions.length - 1];
  const canRetry = ['failed', 'cancelled'].includes(state.status) && !isRunActive(run.status);
  return html`<section class="panel step-detail">
    <header class="panel-row">
      ${step.kind === 'gate' ? html`<span class="tool-badge tool-owner">You</span>` : html`<${ToolBadge} tool=${step.tool} />`}
      <h2 class="panel-title">${step.title}</h2>
      <${StatusPill} status=${state.status} />
      <${AccessBadge} access=${step.access} />
      <span class="spacer"></span>
      ${state.attempts > 1 ? html`<span class="muted small">attempt ${state.attempts}</span>` : null}
      <span class="muted small">${duration(state.startedAt, state.finishedAt)}</span>
      ${canRetry
        ? html`<button class="btn btn-sm" onClick=${() => act(() => api.retryStep(run.id, step.id), 'Retrying step')}>↻ Retry</button>`
        : null}
    </header>
    ${step.profileName ? html`<p class="muted small">Profile: ${step.profileName}</p>` : null}
    ${latest?.patchPath
      ? html`<p class="small">Patch: <span class="mono break">${latest.patchPath}</span></p>`
      : null}
    ${state.error ? html`<${Notice} variant=${state.status === 'cancelled' ? 'warn' : 'error'}>${state.error}<//>` : null}
    ${state.status === 'pending'
      ? html`<p class="muted">Waiting for: ${step.inputs.map((/** @type {string} */ id) => titleOf(run.plan, id)).join(', ') || 'a free slot'}.</p>`
      : null}
    ${state.output
      ? html`<h3 class="panel-subtitle">Output</h3>
          <div class="prose result-text">${state.output}</div>`
      : null}
    ${latest ? html`<${SessionLog} key=${latest.id} sessionId=${latest.id} active=${isActive(latest.status)} />` : null}
    <details>
      <summary>Instructions${sessions.length ? ` and ${String(sessions.length)} session${sessions.length === 1 ? '' : 's'}` : ''}</summary>
      <pre class="code code-scroll">${step.instructions}</pre>
      ${sessions.map(
        (/** @type {any} */ s, /** @type {number} */ i) => html`<p class="muted small session-line">
          #${i + 1} ${s.purpose === 'revision' ? '(revision)' : ''} <${StatusPill} status=${s.status} />
          <${Time} iso=${s.startedAt ?? s.createdAt} /> · ${duration(s.startedAt, s.finishedAt)}
        </p>`,
      )}
      ${latest ? html`<pre class="code code-scroll">${latest.prompt}</pre>` : null}
    </details>
  </section>`;
}

// ---- building blocks ----------------------------------------------------------------

const ACCESS_NOTE = /** @type {Record<string, string>} */ ({
  write: ' · writes files',
  isolated: ' · isolated copy',
});

/** @param {{access: string}} p */
function AccessBadge({ access }) {
  if (access === 'write') return html`<span class="access-badge" title="Changes the task folder; never overlaps another write step">writes files</span>`;
  if (access === 'isolated')
    return html`<span class="access-badge isolated" title="Works in its own git worktree in parallel; changes become a patch for a later write step">isolated copy</span>`;
  return null;
}

/** @param {any} plan @param {string} id */
function titleOf(plan, id) {
  return plan?.steps?.find((/** @type {any} */ s) => s.id === id)?.title ?? id;
}

/** Stage = longest chain of inputs before a step; steps in one stage can run together. */
/** @param {any[]} steps */
function stagesOf(steps) {
  /** @type {Record<string, number>} */
  const depth = {};
  const byId = Object.fromEntries(steps.map((s) => [s.id, s]));
  /** @param {string} id @param {Set<string>} seen @returns {number} */
  const visit = (id, seen) => {
    if (depth[id] !== undefined) return depth[id];
    if (seen.has(id) || !byId[id]) return 0;
    seen.add(id);
    const inputs = byId[id].inputs ?? [];
    const value = inputs.length ? 1 + Math.max(...inputs.map((/** @type {string} */ i) => visit(i, seen))) : 0;
    depth[id] = value;
    return value;
  };
  steps.forEach((s) => visit(s.id, new Set()));
  /** @type {any[][]} */
  const columns = [];
  for (const step of steps) (columns[depth[step.id] ?? 0] ??= []).push(step);
  return columns.filter(Boolean);
}

/** @param {{plan: any, states?: Record<string, any>, selected?: string, onSelect?: (id: string) => void}} p */
function PlanGraph({ plan, states, selected, onSelect }) {
  const columns = useMemo(() => stagesOf(plan?.steps ?? []), [plan]);
  return html`<div class="flow" role="list" aria-label="Workflow steps">
    ${columns.map(
      (col, i) => html`<div class="flow-col" key=${i}>
        <div class="flow-stage">Stage ${i + 1}${col.length > 1 ? ` · ${String(col.length)} in parallel` : ''}</div>
        ${col.map((/** @type {any} */ step) => {
          const status = states?.[step.id]?.status;
          const Tag = onSelect ? 'button' : 'div';
          return html`<${Tag}
            role="listitem"
            type=${onSelect ? 'button' : undefined}
            key=${step.id}
            class=${`flow-step kind-${step.kind} ${status ? `st-${status}` : ''} ${selected === step.id ? 'selected' : ''}`}
            onClick=${onSelect ? () => onSelect(step.id) : undefined}
          >
            <span class="flow-step-head">
              ${step.kind === 'gate' ? html`<span class="tool-badge tool-owner">You</span>` : html`<${ToolBadge} tool=${step.tool} />`}
              ${status ? html`<${StatusPill} status=${status} />` : null}
            </span>
            <span class="flow-step-title">${step.title}</span>
            ${step.kind === 'gate'
              ? html`<span class="muted small">Approve “${titleOf(plan, step.reviews)}”</span>`
              : html`<span class="muted small">${step.profileName}${ACCESS_NOTE[step.access] ?? ''}</span>`}
            ${step.inputs.length
              ? html`<span class="flow-inputs small">← ${step.inputs.map((/** @type {string} */ id) => titleOf(plan, id)).join(', ')}</span>`
              : null}
          <//>`;
        })}
      </div>`,
    )}
  </div>`;
}

/** @param {{plan: any}} p */
function StepList({ plan }) {
  return html`<details class="step-list">
    <summary>Step instructions (${plan.steps.length})</summary>
    <ol>
      ${plan.steps.map(
        (/** @type {any} */ s) => html`<li key=${s.id}>
          <strong>${s.title}</strong>
          <span class="muted small"> · ${s.kind === 'gate' ? 'you approve' : `${toolInfo(s.tool).label}, ${s.profileName}`}</span>
          <${AccessBadge} access=${s.access} />
          ${s.instructions ? html`<pre class="code">${s.instructions}</pre>` : null}
        </li>`,
      )}
    </ol>
  </details>`;
}

/** @param {{label: string, placeholder: string, submitLabel: string, onSend: (text: string) => Promise<void>}} p */
function FeedbackBox({ label, placeholder, submitLabel, onSend }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const send = async (/** @type {Event} */ e) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    await onSend(text.trim());
    setText('');
    setBusy(false);
  };
  return html`<form class="form feedback" onSubmit=${send}>
    <${Field} label=${label}>
      <textarea
        rows="3"
        value=${text}
        placeholder=${placeholder}
        onInput=${(/** @type {Event} */ e) => setText(/** @type {HTMLTextAreaElement} */ (e.currentTarget).value)}
      ></textarea>
    <//>
    <div><button class="btn" type="submit" disabled=${busy || !text.trim()}>${busy ? 'Sending…' : submitLabel}</button></div>
  </form>`;
}

/** @param {{run: any}} p */
function History({ run }) {
  const feedback = /** @type {any[]} */ (run.feedback ?? []);
  const versions = /** @type {any[]} */ (run.planVersions ?? []);
  if (!feedback.length && versions.length < 2 && !run.notes) return null;
  return html`<section class="panel">
    <details>
      <summary>History · ${versions.length} plan version${versions.length === 1 ? '' : 's'}, ${feedback.length} feedback</summary>
      ${run.notes ? html`<p><strong>Your notes:</strong> ${run.notes}</p>` : null}
      <ul class="history">
        ${feedback.map(
          (f) => html`<li key=${f.id}>
            <span class="muted small"><${Time} iso=${f.createdAt} /> → ${f.target === 'plan' ? 'planner' : titleOf(run.plan, f.target)}</span>
            <div class="prose">${f.text}</div>
          </li>`,
        )}
      </ul>
      ${versions.map(
        (v) => html`<details key=${v.version} class="version">
          <summary>Version ${v.version} · <${Time} iso=${v.createdAt} /> · ${v.plan.steps?.length ?? 0} steps</summary>
          <p class="plan-summary">${v.plan.summary}</p>
          <${PlanGraph} plan=${v.plan} />
        </details>`,
      )}
    </details>
  </section>`;
}
