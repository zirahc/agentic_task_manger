import { html, useState } from '../lib/html.js';
import { api, errorMessage } from '../lib/api.js';
import { TOOLS, useResource } from '../lib/util.js';
import {
  Empty,
  Field,
  Loading,
  Modal,
  Notice,
  PageHeader,
  ToolBadge,
  confirmDialog,
  toast,
} from '../components/ui.js';

export const PERMISSION_LABELS = /** @type {Record<string, string>} */ ({
  cli: "CLI's own settings",
  allowedTools: 'Allowed tools',
  full: 'Full access',
});

const MODEL_HINTS = /** @type {Record<string, string>} */ ({
  claude: 'opus',
  codex: 'gpt-6.1-sol',
  antigravity: 'gemini-3-pro',
});

const EMPTY = {
  name: '',
  tool: 'claude',
  model: '',
  effort: '',
  permission: 'cli',
  allowedTools: /** @type {string[]} */ ([]),
  timeoutSeconds: 3600,
  executable: '',
};

export function ProfilesView() {
  const { data, error, loading, reload } = useResource(() => api.profiles(), []);
  const [editing, setEditing] = useState(/** @type {any} */ (null));

  const remove = async (/** @type {any} */ p) => {
    const ok = await confirmDialog({
      title: 'Delete profile',
      message: `Delete "${p.name}"? Roles that use it must be changed first.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteProfile(p.id);
      toast('Profile deleted');
      void reload();
    } catch (e) {
      toast(errorMessage(e), 'error');
    }
  };

  return html`<section class="view">
    <${PageHeader}
      title="Profiles"
      subtitle="How each CLI is called: model, effort, permissions, timeout."
      actions=${html`<button class="btn btn-primary" onClick=${() => setEditing(EMPTY)}>+ New profile</button>`}
    />
    <div class="view-body">
      ${error ? html`<${Notice} variant="error">${errorMessage(error)}<//>` : null}
      ${loading && !data
        ? html`<${Loading} />`
        : data && data.length === 0
          ? html`<${Empty}
              title="No profiles yet"
              action=${html`<button class="btn btn-primary" onClick=${() => setEditing(EMPTY)}>Create a profile</button>`}
              >A profile says how to run Claude Code or Codex. Roles use profiles.<//>`
          : data
            ? html`<div class="table-wrap">
                <table class="table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>CLI</th>
                      <th>Model</th>
                      <th>Effort</th>
                      <th>Permissions</th>
                      <th class="num">Timeout</th>
                      <th class="actions-col"><span class="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    ${data.map(
                      (/** @type {any} */ p) => html`<tr key=${p.id}>
                        <td><strong>${p.name}</strong></td>
                        <td><${ToolBadge} tool=${p.tool} /></td>
                        <td>${p.model || html`<span class="muted">CLI default</span>`}</td>
                        <td>${p.effort || html`<span class="muted">default</span>`}</td>
                        <td>
                          <span class=${p.permission === 'full' ? 'warn-text' : ''}>${PERMISSION_LABELS[p.permission]}</span>
                          ${p.permission === 'allowedTools'
                            ? html`<span class="muted"> (${p.allowedTools.length})</span>`
                            : null}
                        </td>
                        <td class="num">${p.timeoutSeconds}s</td>
                        <td class="actions-col">
                          <button class="btn btn-ghost btn-sm" onClick=${() => setEditing(p)}>Edit</button>
                          <button class="btn btn-ghost btn-sm danger-hover" onClick=${() => remove(p)}>Delete</button>
                        </td>
                      </tr>`,
                    )}
                  </tbody>
                </table>
              </div>`
            : null}
    </div>
    ${editing
      ? html`<${ProfileForm}
          initial=${editing}
          onClose=${() => setEditing(null)}
          onSaved=${() => {
            setEditing(null);
            void reload();
          }}
        />`
      : null}
  </section>`;
}

/** @param {{initial: any, onClose: () => void, onSaved: () => void}} p */
function ProfileForm({ initial, onClose, onSaved }) {
  const [form, setForm] = useState({ ...initial, allowedText: (initial.allowedTools ?? []).join('\n') });
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
      name: form.name,
      tool: form.tool,
      model: form.model,
      effort: form.effort,
      permission: form.permission,
      allowedTools: form.allowedText.split('\n').map((/** @type {string} */ s) => s.trim()).filter(Boolean),
      timeoutSeconds: Number(form.timeoutSeconds),
      executable: form.executable,
    };
    try {
      if (initial.id) await api.updateProfile(initial.id, payload);
      else await api.createProfile(payload);
      toast(initial.id ? 'Profile saved' : 'Profile created');
      onSaved();
    } catch (e2) {
      setErr(e2);
    } finally {
      setSaving(false);
    }
  };

  return html`<${Modal}
    title=${initial.id ? `Edit ${initial.name}` : 'New profile'}
    onClose=${onClose}
    footer=${html`<button type="button" class="btn" onClick=${onClose}>Cancel</button>
      <button type="submit" form="profile-form" class="btn btn-primary" disabled=${saving}>
        ${saving ? 'Saving…' : 'Save'}
      </button>`}
  >
    <form id="profile-form" class="form" onSubmit=${submit}>
      ${err && !err.field ? html`<${Notice} variant="error">${errorMessage(err)}<//>` : null}
      <${Field} label="Name" error=${fieldErr('name')}>
        <input value=${form.name} onInput=${set('name')} placeholder="Claude — Implementation" required />
      <//>
      <div class="form-row">
        <${Field} label="CLI">
          <select
            value=${form.tool}
            onChange=${(/** @type {Event} */ e) => {
              const tool = /** @type {HTMLSelectElement} */ (e.currentTarget).value;
              // Drop choices the new CLI does not support.
              const permission = form.permission === 'allowedTools' && tool !== 'claude' ? 'cli' : form.permission;
              const effort = form.effort === 'max' && tool === 'codex' ? 'xhigh' : form.effort;
              setForm({ ...form, tool, permission, effort });
            }}
          >
            ${TOOLS.map((t) => html`<option value=${t.id}>${t.label}</option>`)}
          </select>
        <//>
        <${Field}
          label="Model"
          hint=${form.tool === 'antigravity' ? 'Blank = default · list with agy models' : "Blank = the CLI's default"}
          error=${fieldErr('model')}
        >
          <input value=${form.model} onInput=${set('model')} placeholder=${MODEL_HINTS[form.tool] ?? ''} />
        <//>
        <${Field} label="Effort" error=${fieldErr('effort')}>
          <select value=${form.effort} onChange=${set('effort')}>
            <option value="">CLI default</option>
            ${['low', 'medium', 'high', 'xhigh', ...(form.tool === 'codex' ? [] : ['max'])].map(
              (level) => html`<option value=${level}>${level}</option>`,
            )}
          </select>
        <//>
      </div>
      <${Field}
        label="Permissions"
        hint="Headless CLIs cannot ask for permission while they run, so this is decided up front."
        error=${fieldErr('permission')}
      >
        <select value=${form.permission} onChange=${set('permission')}>
          <option value="cli">Use the CLI's own settings</option>
          ${form.tool === 'claude' ? html`<option value="allowedTools">Allowed tools (list below)</option>` : null}
          <option value="full">Full access (no permission checks)</option>
        </select>
      <//>
      ${form.permission === 'full'
        ? html`<${Notice} variant="warn">Full access lets the agent edit files and run any command without asking.<//>`
        : null}
      ${form.tool === 'antigravity' && form.permission === 'cli'
        ? html`<${Notice} variant="info">
            Headless <code>agy</code> silently skips any tool it may not use and still reports success. Pre-approve what
            sessions need with <code>permissions.allow</code> rules (for example <code>command(git)</code>${', '}
            <code>write_file(src/)</code>) in <code>~/.gemini/antigravity-cli/settings.json</code>. Skipped tools
            show up as warnings in the session log.
          <//>`
        : null}
      ${form.permission === 'allowedTools'
        ? html`<${Field} label="Allowed tools" hint="One Claude Code rule per line, e.g. Edit, Write, Bash(git status *)" error=${fieldErr('allowedTools')}>
            <textarea rows="4" value=${form.allowedText} onInput=${set('allowedText')}></textarea>
          <//>`
        : null}
      <div class="form-row">
        <${Field} label="Timeout (seconds)" hint="30 – 43200" error=${fieldErr('timeoutSeconds')}>
          <input type="number" min="30" max="43200" value=${form.timeoutSeconds} onInput=${set('timeoutSeconds')} />
        <//>
        <${Field} label="Executable path" hint="Blank = find it on PATH" error=${fieldErr('executable')}>
          <input value=${form.executable} onInput=${set('executable')} placeholder="auto-detect" />
        <//>
      </div>
    </form>
  <//>`;
}
