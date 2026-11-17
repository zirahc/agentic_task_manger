import { html } from '../lib/html.js';
import { api, errorMessage } from '../lib/api.js';
import { TOOLS, duration, isActive, navigate, useResource } from '../lib/util.js';
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

export function SessionsView() {
  const sessions = useResource(() => api.sessions(false), [], 2000);
  const settings = useResource(() => api.settings(), []);
  const list = /** @type {any[]} */ (sessions.data ?? []);
  const active = list.filter((s) => isActive(s.status));
  const recent = list.filter((s) => !isActive(s.status)).slice(0, 50);
  const limits = /** @type {any} */ (settings.data)?.maxConcurrent ?? {};

  const cancel = async (/** @type {any} */ s) => {
    const ok = await confirmDialog({
      title: 'Stop session',
      message: `Stop the ${s.roleName} session on "${s.taskTitle}"?`,
      confirmLabel: 'Stop',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.cancelSession(s.id);
      toast('Stopping…', 'info');
      void sessions.reload();
    } catch (e) {
      toast(errorMessage(e), 'error');
    }
  };

  return html`<section class="view">
    <${PageHeader} title="Sessions" subtitle="Live CLI sessions and capacity." />
    <div class="view-body">
      ${sessions.error ? html`<${Notice} variant="error">${errorMessage(sessions.error)}<//>` : null}
      <div class="capacity">
        ${TOOLS.map(({ id: tool }) => {
          const running = active.filter((s) => s.tool === tool && s.status === 'running').length;
          const queued = active.filter((s) => s.tool === tool && s.status === 'queued').length;
          const max = limits[tool] ?? 0;
          return html`<div class="panel capacity-card" key=${tool}>
            <${ToolBadge} tool=${tool} />
            <div class="capacity-num"><strong>${running}</strong> / ${max} running</div>
            <div class="meter" role="meter" aria-valuemin="0" aria-valuemax=${max} aria-valuenow=${running} aria-label=${`${tool} sessions running`}>
              <span style=${`width:${max ? Math.min(100, (running / max) * 100) : 0}%`}></span>
            </div>
            <div class="muted small">${queued} queued</div>
          </div>`;
        })}
      </div>
      <h2 class="section-title">Active</h2>
      ${sessions.loading && !sessions.data
        ? html`<${Loading} />`
        : active.length === 0
          ? html`<p class="muted">No sessions are running.</p>`
          : html`<${SessionTable} rows=${active} onCancel=${cancel} />`}
      <h2 class="section-title">Recent</h2>
      ${recent.length === 0 && sessions.data
        ? html`<${Empty} title="No finished sessions yet" />`
        : html`<${SessionTable} rows=${recent} />`}
    </div>
  </section>`;
}

/** @param {{rows: any[], onCancel?: (s: any) => void}} p */
function SessionTable({ rows, onCancel }) {
  return html`<div class="table-wrap">
    <table class="table table-click">
      <thead>
        <tr>
          <th>Status</th>
          <th>Role</th>
          <th>Task</th>
          <th>Started</th>
          <th class="num">Duration</th>
          ${onCancel ? html`<th class="actions-col"><span class="sr-only">Actions</span></th>` : null}
        </tr>
      </thead>
      <tbody>
        ${rows.map(
          (s) => html`<tr key=${s.id} onClick=${() => navigate(`/runs/${s.runId}`)}>
            <td><${StatusPill} status=${s.status} /></td>
            <td><${ToolBadge} tool=${s.tool} /> ${s.roleName}</td>
            <td class="ellipsis" title=${s.taskTitle}>${s.taskTitle}</td>
            <td><${Time} iso=${s.startedAt ?? s.createdAt} /></td>
            <td class="num">${duration(s.startedAt, s.finishedAt)}</td>
            ${onCancel
              ? html`<td class="actions-col">
                  <button
                    class="btn btn-ghost btn-sm danger-hover"
                    onClick=${(/** @type {Event} */ e) => {
                      e.stopPropagation();
                      onCancel(s);
                    }}
                  >
                    Stop
                  </button>
                </td>`
              : null}
          </tr>`,
        )}
      </tbody>
    </table>
  </div>`;
}
