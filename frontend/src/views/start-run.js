import { html, useEffect, useState } from '../lib/html.js';
import { api, errorMessage } from '../lib/api.js';
import { navigate, useResource } from '../lib/util.js';
import { Field, Loading, Modal, Notice, ToolBadge } from '../components/ui.js';
import { PERMISSION_LABELS } from './profiles.js';

/** Pick a role, preview exactly what will run, then start explicitly. */
/** @param {{task: any, onClose: () => void, onStarted: (run: any) => void}} p */
export function StartRunDialog({ task, onClose, onStarted }) {
  const roles = useResource(() => api.roles(), []);
  const [roleId, setRoleId] = useState('');
  const [preview, setPreview] = useState(/** @type {any} */ (null));
  const [previewErr, setPreviewErr] = useState(/** @type {unknown} */ (null));
  const [starting, setStarting] = useState(false);
  const list = /** @type {any[]} */ (roles.data ?? []);

  useEffect(() => {
    if (!roleId && list[0]) setRoleId(list[0].id);
  }, [roles.data]);

  useEffect(() => {
    if (!roleId) return;
    let live = true;
    setPreview(null);
    setPreviewErr(null);
    api
      .previewRun(task.id, { roleId, mode: 'single' })
      .then((p) => live && setPreview(p))
      .catch((e) => live && setPreviewErr(e));
    return () => {
      live = false;
    };
  }, [roleId]);

  const start = async () => {
    setStarting(true);
    try {
      onStarted(await api.startRun(task.id, { roleId, mode: 'single' }));
    } catch (e) {
      setPreviewErr(e);
      setStarting(false);
    }
  };

  const s = preview?.sessions?.[0];

  return html`<${Modal}
    wide
    title=${`Start run · ${task.title}`}
    onClose=${onClose}
    footer=${html`<button type="button" class="btn" onClick=${onClose}>Cancel</button>
      <button type="button" class="btn btn-primary" disabled=${!s || starting} onClick=${start}>
        ${starting ? 'Starting…' : '▶ Start'}
      </button>`}
  >
    ${roles.loading && !roles.data
      ? html`<${Loading} />`
      : list.length === 0
        ? html`<${Notice} variant="info">
            No roles yet. A run needs at least one role.
            <button class="btn btn-sm" onClick=${() => navigate('/roles')}>Create a role</button>
          <//>`
        : html`<div class="form">
            <div class="form-row">
              <${Field} label="Mode" hint="More modes (pipeline, parallel, review loop) come with orchestration.">
                <select disabled><option>Single session</option></select>
              <//>
              <${Field} label="Role">
                <select value=${roleId} onChange=${(/** @type {Event} */ e) => setRoleId(/** @type {HTMLSelectElement} */ (e.currentTarget).value)}>
                  ${list.map((r) => html`<option value=${r.id}>${r.name}</option>`)}
                </select>
              <//>
            </div>
            ${previewErr ? html`<${Notice} variant="error">${errorMessage(previewErr)}<//>` : null}
            ${!s && !previewErr ? html`<${Loading} />` : null}
            ${s
              ? html`<section class="preview">
                  <h3 class="panel-subtitle">What will run</h3>
                  <dl class="meta meta-2col">
                    <dt>CLI</dt>
                    <dd><${ToolBadge} tool=${s.tool} /> ${s.model || html`<span class="muted">default model</span>`}</dd>
                    <dt>Permissions</dt>
                    <dd class=${s.permission === 'full' ? 'warn-text' : ''}>${PERMISSION_LABELS[s.permission]}</dd>
                    <dt>Folder</dt>
                    <dd class="mono break">${s.workdir}</dd>
                    <dt>Timeout</dt>
                    <dd>${s.timeoutSeconds}s</dd>
                  </dl>
                  <${Field} label="Command">
                    <pre class="code">${s.command}</pre>
                  <//>
                  <details>
                    <summary>Prompt (${s.prompt.length} chars, sent on stdin)</summary>
                    <pre class="code code-scroll">${s.prompt}</pre>
                  </details>
                  ${s.permission === 'full'
                    ? html`<${Notice} variant="warn">This session runs with full access: no permission checks.<//>`
                    : null}
                  ${s.tool === 'antigravity' && s.permission === 'cli'
                    ? html`<${Notice} variant="info">
                        Antigravity skips tools that its <code>settings.json</code> does not pre-approve, without
                        failing the run. Watch for warnings in the session log.
                      <//>`
                    : null}
                  <p class="muted small">
                    The CLI may send the prompt and repository content to its own provider. Nothing runs until you press Start.
                  </p>
                </section>`
              : null}
          </div>`}
  <//>`;
}
