import { html, useEffect, useRef, useState } from '../lib/html.js';
import { api, errorMessage } from '../lib/api.js';
import { duration, formatNumber, isActive, isRunActive, navigate, toolInfo, useResource } from '../lib/util.js';
import { PlannedRun } from './plan.js';
import {
  Empty,
  Loading,
  Notice,
  PageHeader,
  StatusPill,
  Time,
  ToolBadge,
  confirmDialog,
  toast,
} from '../components/ui.js';
import { PERMISSION_LABELS } from './profiles.js';

export function RunsView() {
  const runs = useResource(() => api.runs(undefined), [], 3000);
  const list = /** @type {any[]} */ (runs.data ?? []);
  return html`<section class="view">
    <${PageHeader} title="Runs" subtitle="Every execution of a task, newest first." />
    <div class="view-body">
      ${runs.error ? html`<${Notice} variant="error">${errorMessage(runs.error)}<//>` : null}
      ${runs.loading && !runs.data
        ? html`<${Loading} />`
        : list.length === 0
          ? html`<${Empty} title="No runs yet">Open a task and press <strong>Start run</strong>.<//>`
          : html`<div class="table-wrap">
              <table class="table table-click">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Task</th>
                    <th>Sessions</th>
                    <th>Started</th>
                    <th class="num">Duration</th>
                  </tr>
                </thead>
                <tbody>
                  ${list.map(
                    (r) => html`<tr key=${r.id} onClick=${() => navigate(`/runs/${r.id}`)}>
                      <td><${StatusPill} status=${r.status} /></td>
                      <td><a href=${`#/runs/${encodeURIComponent(r.id)}`}>${r.taskTitle}</a></td>
                      <td><${RunSessions} run=${r} /></td>
                      <td><${Time} iso=${r.createdAt} /></td>
                      <td class="num">${duration(r.mode === 'planned' ? r.createdAt : r.sessions[0]?.startedAt, r.finishedAt)}</td>
                    </tr>`,
                  )}
                </tbody>
              </table>
            </div>`}
    </div>
  </section>`;
}

/** What a run is made of: its roles, or for AI workflows the tools and step count. */
/** @param {{run: any}} p */
export function RunSessions({ run }) {
  if (run.mode === 'planned') {
    const tools = [...new Set((run.plan?.steps ?? []).map((/** @type {any} */ s) => s.tool).filter(Boolean))];
    return html`<span class="run-role">
      <span class="ai-badge">✦ AI workflow</span>
      ${run.plan ? html`<span class="muted small">v${run.planVersion} · ${run.plan.steps.length} steps</span>` : html`<span class="muted small">planning</span>`}
      ${tools.map((t) => html`<${ToolBadge} tool=${t} />`)}
    </span>`;
  }
  return run.sessions.map(
    (/** @type {any} */ s) => html`<span class="run-role"><${ToolBadge} tool=${s.tool} /> ${s.roleName}</span>`,
  );
}

/** @param {{id: string}} p */
export function RunDetail({ id }) {
  const [poll, setPoll] = useState(/** @type {number | null} */ (1500));
  const run = useResource(() => api.run(id), [id], poll);
  const r = /** @type {any} */ (run.data);
  const active = r ? isRunActive(r.status) : true;
  const open = r ? active || r.status === 'awaiting-approval' : false;
  const planned = r?.mode === 'planned';

  useEffect(() => {
    setPoll(active ? 1500 : null);
  }, [active]);

  const cancel = async () => {
    const ok = await confirmDialog({
      title: 'Cancel run',
      message: 'Stop every session in this run? Running CLI processes are killed; their output so far is kept.',
      confirmLabel: 'Cancel run',
      danger: true,
    });
    if (!ok) return;
    try {
      run.setData(await api.cancelRun(id));
      toast('Cancelling…', 'info');
    } catch (e) {
      toast(errorMessage(e), 'error');
    }
  };

  const remove = async () => {
    const ok = await confirmDialog({
      title: 'Delete run',
      message: 'Delete this run and its session logs?',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteRun(id);
      toast('Run deleted');
      navigate(`/tasks/${r.taskId}`);
    } catch (e) {
      toast(errorMessage(e), 'error');
    }
  };

  if (run.error && !r) {
    return html`<section class="view">
      <${PageHeader} title="Run" actions=${html`<a class="btn" href="#/runs">← Runs</a>`} />
      <div class="view-body"><${Notice} variant="error">${errorMessage(run.error)}<//></div>
    </section>`;
  }
  if (!r) return html`<section class="view"><${Loading} /></section>`;

  return html`<section class="view">
    <${PageHeader}
      title=${r.taskTitle}
      subtitle=${planned ? `AI workflow · ${r.sessions.length} sessions` : `Run ${r.id} · ${r.mode}`}
      actions=${html`
        <a class="btn" href=${`#/tasks/${encodeURIComponent(r.taskId)}`}>← Task</a>
        ${open
          ? html`<button class="btn btn-danger" onClick=${cancel}>■ Cancel run</button>`
          : html`<button class="btn btn-ghost danger-hover" onClick=${remove}>Delete</button>`}
      `}
    />
    <div class="view-body">
      <div class="run-summary">
        <${StatusPill} status=${r.status} />
        <span class="muted">Started <${Time} iso=${r.createdAt} /></span>
        <span class="muted">Duration ${duration(planned ? r.createdAt : r.sessions[0]?.startedAt, r.finishedAt)}</span>
        <${UsageTotal} sessions=${r.sessions} />
      </div>
      ${planned
        ? html`<${PlannedRun} run=${r} onChange=${run.setData} />`
        : r.sessions.map((/** @type {any} */ s) => html`<${SessionCard} key=${s.id} session=${s} />`)}
    </div>
  </section>`;
}

/** Totals across a run's sessions; CLI-reported cost only. */
/** @param {{sessions: any[]}} p */
function UsageTotal({ sessions }) {
  let tokens = 0;
  let cost = 0;
  let costed = 0;
  for (const s of sessions) {
    const u = s.usage ?? {};
    tokens += (u.inputTokens ?? 0) + (u.outputTokens ?? 0);
    if (typeof u.costUsd === 'number') {
      cost += u.costUsd;
      costed += 1;
    }
  }
  if (!tokens) return null;
  return html`<span class="muted" title="Input + output tokens reported by the CLIs">
    ${formatNumber(tokens)} tokens${costed ? ` · USD ${cost.toFixed(4)} (CLI-reported, ${String(costed)}/${String(sessions.length)} sessions)` : ''}
  </span>`;
}

/** @param {{session: any}} p */
function SessionCard({ session: s }) {
  const usage = s.usage ?? {};
  return html`<article class="panel session-card">
    <header class="session-head">
      <${ToolBadge} tool=${s.tool} />
      <h2>${s.roleName}</h2>
      <${StatusPill} status=${s.status} />
      <span class="muted small">${s.model || 'default model'} · ${PERMISSION_LABELS[s.permission] ?? s.permission}</span>
      <span class="spacer"></span>
      <span class="muted small">${duration(s.startedAt, s.finishedAt)}</span>
    </header>
    ${s.status === 'queued'
      ? html`<${Notice} variant="info">Waiting for a free ${toolInfo(s.tool).label} slot (see Settings → concurrency).<//>`
      : null}
    ${s.error ? html`<${Notice} variant=${s.status === 'cancelled' ? 'warn' : 'error'}>${s.error}<//>` : null}
    ${s.resultText
      ? html`<section class="result">
          <h3 class="panel-subtitle">Result</h3>
          <div class="prose result-text">${s.resultText}</div>
        </section>`
      : null}
    ${usage.inputTokens || usage.outputTokens
      ? html`<p class="usage-line muted small">
          Tokens: ${formatNumber(usage.inputTokens)} in · ${formatNumber(usage.cacheReadTokens)} cached ·
          ${formatNumber(usage.outputTokens)} out
          ${usage.thinkingTokens ? ` · ${formatNumber(usage.thinkingTokens)} thinking` : ''}
          ${typeof usage.costUsd === 'number' ? html` · <strong>USD ${usage.costUsd.toFixed(4)}</strong> (CLI-reported)` : null}
        </p>`
      : null}
    <${SessionLog} sessionId=${s.id} active=${isActive(s.status)} />
    <details class="session-extra">
      <summary>Prompt and details</summary>
      <dl class="meta meta-2col">
        <dt>Folder</dt>
        <dd class="mono break">${s.workdir}</dd>
        <dt>CLI session</dt>
        <dd class="mono">${s.cliSessionId ?? '—'}</dd>
        <dt>Exit code</dt>
        <dd>${s.exitCode ?? '—'}</dd>
      </dl>
      <pre class="code code-scroll">${s.prompt}</pre>
    </details>
  </article>`;
}

const ICONS = /** @type {Record<string, string>} */ ({
  init: '●',
  message: '💬',
  command: '$',
  file: '✎',
  tool: '⚙',
  'tool-error': '⚠',
  warning: '⚠',
  error: '✖',
  result: '✔',
  output: '›',
  stderr: '!',
});

/** Live, readable activity log. Polls only while the session is active. */
/** @param {{sessionId: string, active: boolean}} p */
export function SessionLog({ sessionId, active }) {
  const [events, setEvents] = useState(/** @type {any[]} */ ([]));
  const [err, setErr] = useState(/** @type {unknown} */ (null));
  const [showNoise, setShowNoise] = useState(false);
  const lastId = useRef(0);
  const busy = useRef(false);
  const again = useRef(false);
  const box = useRef(/** @type {HTMLDivElement | null} */ (null));
  const stick = useRef(true);

  // One fetch at a time: overlapping fetches would append the same events twice.
  const fetchMore = async () => {
    if (busy.current) {
      again.current = true;
      return;
    }
    busy.current = true;
    try {
      do {
        again.current = false;
        // Drain in pages so a long log loads fully.
        for (let i = 0; i < 20; i++) {
          const res = await api.sessionEvents(sessionId, lastId.current);
          if (!res.events.length) break;
          lastId.current = res.events[res.events.length - 1].id;
          setEvents((list) => [...list, ...res.events]);
          if (res.events.length < 500) break;
        }
      } while (again.current);
      setErr(null);
    } catch (e) {
      setErr(e);
    } finally {
      busy.current = false;
    }
  };

  // Initial load, and one final fetch when the session ends (the card is keyed by session id).
  useEffect(() => {
    void fetchMore();
    if (!active) return undefined;
    const t = setInterval(() => void fetchMore(), 1500);
    return () => clearInterval(t);
  }, [active, sessionId]);

  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events, showNoise]);

  const visible = showNoise ? events : events.filter((e) => e.kind !== 'stderr' && e.kind !== 'output');
  const hidden = events.length - visible.length;

  return html`<section class="log-wrap">
    <header class="log-head">
      <h3 class="panel-subtitle">Activity ${active ? html`<span class="live-badge">LIVE</span>` : null}</h3>
      <span class="spacer"></span>
      ${hidden > 0 || showNoise
        ? html`<label class="check small">
            <input type="checkbox" checked=${showNoise} onChange=${() => setShowNoise(!showNoise)} />
            Raw output and stderr${hidden ? ` (${String(hidden)})` : ''}
          </label>`
        : null}
    </header>
    ${err ? html`<${Notice} variant="error">${errorMessage(err)}<//>` : null}
    <div
      class="log"
      ref=${box}
      onScroll=${(/** @type {Event} */ e) => {
        const el = /** @type {HTMLDivElement} */ (e.currentTarget);
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
    >
      ${visible.length === 0
        ? html`<p class="muted small log-empty">${active ? 'Waiting for output…' : 'No activity recorded.'}</p>`
        : visible.map((e) => html`<${LogLine} key=${e.id} event=${e} />`)}
    </div>
  </section>`;
}

/** @param {{event: any}} p */
function LogLine({ event: e }) {
  const d = e.data ?? {};
  /** @type {unknown} */
  let body;
  switch (e.kind) {
    case 'init':
      body = html`Session started${d.model ? html` · <span class="mono">${d.model}</span>` : null}`;
      break;
    case 'message':
      body = html`<span class="log-message">${d.text}</span>`;
      break;
    case 'command':
      body = html`<span class="mono">${d.command}</span>
        ${typeof d.exitCode === 'number'
          ? html`<span class=${`exit ${d.exitCode === 0 ? 'exit-ok' : 'exit-bad'}`}>exit ${d.exitCode}</span>`
          : null}`;
      break;
    case 'file':
      body = html`<span class="muted">${d.action}</span> <span class="mono">${d.path}</span>`;
      break;
    case 'tool':
      body = html`<strong>${d.name}</strong> <span class="muted">${d.summary}</span>`;
      break;
    case 'result':
      body = html`<strong>${d.isError ? 'Finished with an error' : 'Finished'}</strong>`;
      break;
    default:
      body = html`<span class="mono">${d.text}</span>`;
  }
  return html`<div class=${`log-line log-${e.kind}`}>
    <span class="log-icon" aria-hidden="true">${ICONS[e.kind] ?? '·'}</span>
    <span class="log-body">${body}</span>
    <span class="log-time"><${Time} iso=${e.createdAt} /></span>
  </div>`;
}
