import { html, useState } from '../lib/html.js';
import { api, errorMessage } from '../lib/api.js';
import { navigate, useResource } from '../lib/util.js';
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

const COLORS = ['#6d7cff', '#34d399', '#f59e0b', '#f472b6', '#22d3ee', '#a78bfa', '#f87171'];

const STARTERS = [
  {
    name: 'Planner',
    description: 'Reads the code and writes a step-by-step plan. Does not edit files.',
    instructions:
      'Read the repository and produce a concrete implementation plan: the steps, the files to change, and the risks. Do not modify any files.',
  },
  {
    name: 'Implementer',
    description: 'Makes the change and runs the tests.',
    instructions:
      'Implement the task. Keep changes focused, follow the repository conventions, and run the relevant tests before finishing. End with a short summary of what changed.',
  },
  {
    name: 'Reviewer',
    description: 'Reviews the current changes and reports findings by severity.',
    instructions:
      'Review the uncommitted changes in this repository for correctness, security and test coverage. Report findings as P0–P3 with file and line. Do not modify files.',
  },
];

export function RolesView() {
  const roles = useResource(() => api.roles(), []);
  const profiles = useResource(() => api.profiles(), []);
  const [editing, setEditing] = useState(/** @type {any} */ (null));
  const profileList = /** @type {any[]} */ (profiles.data ?? []);
  const byId = Object.fromEntries(profileList.map((p) => [p.id, p]));

  const newRole = (/** @type {any} */ starter = {}) =>
    setEditing({
      name: '',
      description: '',
      instructions: '',
      color: COLORS[(roles.data?.length ?? 0) % COLORS.length],
      profileId: profileList[0]?.id ?? '',
      workdirMode: 'task',
      fixedDir: '',
      timeoutSeconds: null,
      ...starter,
    });

  const remove = async (/** @type {any} */ r) => {
    const ok = await confirmDialog({
      title: 'Delete role',
      message: `Delete "${r.name}"? Past runs keep their own copy of it.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteRole(r.id);
      toast('Role deleted');
      void roles.reload();
    } catch (e) {
      toast(errorMessage(e), 'error');
    }
  };

  const noProfiles = profiles.data && profileList.length === 0;

  return html`<section class="view">
    <${PageHeader}
      title="Roles"
      subtitle="What each session does: its instructions, CLI profile and working folder."
      actions=${html`<button class="btn btn-primary" disabled=${noProfiles} onClick=${() => newRole()}>+ New role</button>`}
    />
    <div class="view-body">
      ${roles.error ? html`<${Notice} variant="error">${errorMessage(roles.error)}<//>` : null}
      ${noProfiles
        ? html`<${Notice} variant="info">
            Create a profile first: a role runs through a CLI profile.
            <button class="btn btn-sm" onClick=${() => navigate('/profiles')}>Go to Profiles</button>
          <//>`
        : null}
      ${roles.loading && !roles.data
        ? html`<${Loading} />`
        : roles.data && roles.data.length === 0
          ? html`<${Empty} title="No roles yet">
              Start from a common role or create your own.
              <div class="starter-row">
                ${STARTERS.map(
                  (s) => html`<button class="btn" disabled=${noProfiles} onClick=${() => newRole(s)}>+ ${s.name}</button>`,
                )}
              </div>
            <//>`
          : html`<div class="card-grid">
              ${(roles.data ?? []).map(
                (/** @type {any} */ r) => html`<article class="card role-card" key=${r.id} style=${`--accent:${r.color}`}>
                  <header class="card-head">
                    <span class="role-dot" aria-hidden="true"></span>
                    <h3>${r.name}</h3>
                    ${byId[r.profileId] ? html`<${ToolBadge} tool=${byId[r.profileId].tool} />` : null}
                  </header>
                  ${r.description ? html`<p class="muted">${r.description}</p>` : null}
                  <dl class="meta">
                    <dt>Profile</dt>
                    <dd>${byId[r.profileId]?.name ?? html`<span class="warn-text">missing</span>`}</dd>
                    <dt>Folder</dt>
                    <dd>${r.workdirMode === 'fixed' ? r.fixedDir : "task's repository"}</dd>
                    ${r.timeoutSeconds ? html`<dt>Timeout</dt><dd>${r.timeoutSeconds}s</dd>` : null}
                  </dl>
                  ${r.instructions ? html`<p class="clamp-3 instructions">${r.instructions}</p>` : null}
                  <footer class="card-actions">
                    <button class="btn btn-ghost btn-sm" onClick=${() => setEditing(r)}>Edit</button>
                    <button class="btn btn-ghost btn-sm danger-hover" onClick=${() => remove(r)}>Delete</button>
                  </footer>
                </article>`,
              )}
            </div>`}
    </div>
    ${editing
      ? html`<${RoleForm}
          initial=${editing}
          profiles=${profileList}
          onClose=${() => setEditing(null)}
          onSaved=${() => {
            setEditing(null);
            void roles.reload();
          }}
        />`
      : null}
  </section>`;
}

/** @param {{initial: any, profiles: any[], onClose: () => void, onSaved: () => void}} p */
function RoleForm({ initial, profiles, onClose, onSaved }) {
  const [form, setForm] = useState({ ...initial, timeoutText: initial.timeoutSeconds ? String(initial.timeoutSeconds) : '' });
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
      description: form.description,
      color: form.color,
      profileId: form.profileId,
      instructions: form.instructions,
      workdirMode: form.workdirMode,
      fixedDir: form.fixedDir,
      timeoutSeconds: form.timeoutText.trim() ? Number(form.timeoutText) : null,
    };
    try {
      if (initial.id) await api.updateRole(initial.id, payload);
      else await api.createRole(payload);
      toast(initial.id ? 'Role saved' : 'Role created');
      onSaved();
    } catch (e2) {
      setErr(e2);
    } finally {
      setSaving(false);
    }
  };

  return html`<${Modal}
    wide
    title=${initial.id ? `Edit ${initial.name}` : 'New role'}
    onClose=${onClose}
    footer=${html`<button type="button" class="btn" onClick=${onClose}>Cancel</button>
      <button type="submit" form="role-form" class="btn btn-primary" disabled=${saving}>${saving ? 'Saving…' : 'Save'}</button>`}
  >
    <form id="role-form" class="form" onSubmit=${submit}>
      ${err && !err.field ? html`<${Notice} variant="error">${errorMessage(err)}<//>` : null}
      <div class="form-row">
        <${Field} label="Name" error=${fieldErr('name')}>
          <input value=${form.name} onInput=${set('name')} placeholder="Implementer" required />
        <//>
        <${Field} label="Profile" error=${fieldErr('profileId')}>
          <select value=${form.profileId} onChange=${set('profileId')}>
            ${profiles.map((p) => html`<option value=${p.id}>${p.name} (${p.tool}${p.model ? ` · ${p.model}` : ''})</option>`)}
          </select>
        <//>
      </div>
      <${Field} label="Description" error=${fieldErr('description')}>
        <input value=${form.description} onInput=${set('description')} placeholder="One line about what this role does" />
      <//>
      <${Field}
        label="Instructions"
        hint="Sent at the top of every prompt this role receives, before the task."
        error=${fieldErr('instructions')}
      >
        <textarea rows="8" value=${form.instructions} onInput=${set('instructions')}></textarea>
      <//>
      <div class="form-row">
        <${Field} label="Working folder">
          <select value=${form.workdirMode} onChange=${set('workdirMode')}>
            <option value="task">The task's repository folder</option>
            <option value="fixed">A fixed folder</option>
          </select>
        <//>
        ${form.workdirMode === 'fixed'
          ? html`<${Field} label="Fixed folder" error=${fieldErr('fixedDir')}>
              <input value=${form.fixedDir} onInput=${set('fixedDir')} placeholder="C:\\work\\repo" />
            <//>`
          : null}
        <${Field} label="Timeout override (s)" hint="Blank = the profile's timeout" error=${fieldErr('timeoutSeconds')}>
          <input type="number" min="30" max="43200" value=${form.timeoutText} onInput=${set('timeoutText')} />
        <//>
      </div>
      <${Field} label="Color">
        <div class="swatches" role="radiogroup" aria-label="Color">
          ${COLORS.map(
            (c) => html`<button
              type="button"
              role="radio"
              aria-checked=${form.color === c}
              aria-label=${c}
              class=${`swatch ${form.color === c ? 'selected' : ''}`}
              style=${`background:${c}`}
              onClick=${() => setForm({ ...form, color: c })}
            ></button>`,
          )}
        </div>
      <//>
    </form>
  <//>`;
}
