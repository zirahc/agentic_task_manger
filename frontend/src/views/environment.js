import { html, useState } from '../lib/html.js';
import { api, errorMessage } from '../lib/api.js';
import { TOOLS, readLocal, toolInfo, useResource, writeLocal } from '../lib/util.js';
import { Field, Loading, Notice, PageHeader, toast } from '../components/ui.js';

export function EnvironmentView() {
  const env = useResource(() => api.environment(), []);
  const data = /** @type {any} */ (env.data);
  return html`<section class="view">
    <${PageHeader}
      title="Environment"
      subtitle="The local tools this dashboard drives."
      actions=${html`<button class="btn" disabled=${env.loading} onClick=${() => env.reload()}>${env.loading ? 'Checking…' : 'Refresh'}</button>`}
    />
    <div class="view-body">
      ${env.error ? html`<${Notice} variant="error">${errorMessage(env.error)}<//>` : null}
      ${data?.demoMode
        ? html`<${Notice} variant="warn">
            Demo mode: sessions run a built-in fake CLI instead of Claude Code, Codex and Antigravity. Start the server without
            <code>DASH_FAKE_CLI=1</code> to use the real CLIs.
          <//>`
        : null}
      ${!data
        ? html`<${Loading} />`
        : html`<div class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>Tool</th>
                  <th>Status</th>
                  <th>Version</th>
                  <th>Path</th>
                </tr>
              </thead>
              <tbody>
                ${data.tools.map(
                  (/** @type {any} */ t) => html`<tr key=${t.name}>
                    <td>
                      <strong>${toolInfo(t.name).label}</strong>
                      ${t.command && t.command !== t.name ? html` <span class="muted mono small">(${t.command})</span>` : null}
                    </td>
                    <td>${t.found ? html`<span class="pill status-succeeded">found</span>` : html`<span class="pill status-failed">missing</span>`}</td>
                    <td>${t.version ?? html`<span class="muted">${t.error ?? '—'}</span>`}</td>
                    <td class="mono break small">${t.path ?? '—'}</td>
                  </tr>`,
                )}
              </tbody>
            </table>
          </div>
          <p class="muted small">
            Install and sign in to the CLIs yourself (for example${' '}
            <code>npm install -g @anthropic-ai/claude-code</code>${' and '}
            <code>npm install -g @openai/codex</code>; Antigravity's <code>agy</code> comes from the Antigravity
            installer), then press Refresh. Run each CLI once interactively to sign in: headless runs reuse that
            sign-in. This dashboard never stores API keys.
          </p>`}
    </div>
  </section>`;
}

/** @param {{settings: any, onSaved: (s: any) => void}} p */
function PlannerSettings({ settings, onSaved }) {
  const profiles = useResource(() => api.profiles(), []);
  const [profileId, setProfileId] = useState(settings.planner.profileId);
  const [guidance, setGuidance] = useState(settings.planner.guidance);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(/** @type {any} */ (null));
  const dirty = profileId !== settings.planner.profileId || guidance !== settings.planner.guidance;

  const save = async (/** @type {Event} */ e) => {
    e.preventDefault();
    setSaving(true);
    setErr(null);
    try {
      onSaved(await api.saveSettings({ planner: { profileId, guidance } }));
      toast('Planner settings saved');
    } catch (e2) {
      setErr(e2);
    } finally {
      setSaving(false);
    }
  };

  return html`<form class="panel form" onSubmit=${save}>
    <h2 class="panel-title">AI planner</h2>
    <p class="muted small">
      <strong>Plan with AI</strong> asks this profile to design a workflow for a task: which agents, how many sessions,
      what each does and in which order. You review and approve every plan before it runs.
    </p>
    ${err && !err.field ? html`<${Notice} variant="error">${errorMessage(err)}<//>` : null}
    <${Field} label="Default planner" hint="Blank = the first Claude Code profile." error=${err?.field === 'profileId' ? err.message : null}>
      <select value=${profileId} onChange=${(/** @type {Event} */ e) => setProfileId(/** @type {HTMLSelectElement} */ (e.currentTarget).value)}>
        <option value="">Automatic (first Claude Code profile)</option>
        ${/** @type {any[]} */ (profiles.data ?? []).map(
          (p) => html`<option value=${p.id}>${p.name} (${toolInfo(p.tool).label})</option>`,
        )}
      </select>
    <//>
    <${Field} label="Guidance for the planner" hint="What each tool is good at, and your house rules. Sent with every plan request.">
      <textarea rows="9" value=${guidance} onInput=${(/** @type {Event} */ e) => setGuidance(/** @type {HTMLTextAreaElement} */ (e.currentTarget).value)}></textarea>
    <//>
    <div class="row-actions">
      <button class="btn btn-primary" type="submit" disabled=${saving || !dirty}>${saving ? 'Saving…' : 'Save'}</button>
      <button class="btn btn-ghost" type="button" disabled=${guidance === settings.defaultGuidance} onClick=${() => setGuidance(settings.defaultGuidance)}>
        Reset guidance to default
      </button>
    </div>
  </form>`;
}

export function SettingsView() {
  const settings = useResource(() => api.settings(), []);
  const [draft, setDraft] = useState(/** @type {any} */ (null));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(/** @type {any} */ (null));
  const [theme, setTheme] = useState(readLocal('dash.theme', 'dark'));
  const limits = draft ?? /** @type {any} */ (settings.data)?.maxConcurrent;

  const save = async (/** @type {Event} */ e) => {
    e.preventDefault();
    setSaving(true);
    setErr(null);
    try {
      const res = await api.saveSettings({
        maxConcurrent: Object.fromEntries(TOOLS.map((t) => [t.id, Number(limits[t.id])])),
      });
      settings.setData(res);
      setDraft(null);
      toast('Settings saved');
    } catch (e2) {
      setErr(e2);
    } finally {
      setSaving(false);
    }
  };

  const changeTheme = (/** @type {Event} */ e) => {
    const value = /** @type {HTMLSelectElement} */ (e.currentTarget).value;
    setTheme(value);
    writeLocal('dash.theme', value);
    const light = value === 'light' || (value === 'system' && matchMedia('(prefers-color-scheme: light)').matches);
    document.documentElement.dataset.theme = light ? 'light' : 'dark';
  };

  return html`<section class="view">
    <${PageHeader} title="Settings" subtitle="Capacity and appearance." />
    <div class="view-body narrow">
      <form class="panel form" onSubmit=${save}>
        <h2 class="panel-title">Concurrent sessions</h2>
        <p class="muted small">
          The most sessions of each CLI that run at the same time. Extra sessions wait in a queue. Keep this within your
          subscription's limits.
        </p>
        ${err && !err.field ? html`<${Notice} variant="error">${errorMessage(err)}<//>` : null}
        ${!limits
          ? html`<${Loading} />`
          : html`<div class="form-row">
              ${TOOLS.map(
                ({ id: tool, label }) => html`<${Field}
                  key=${tool}
                  label=${label}
                  hint="1 – 16"
                  error=${err?.field === tool ? err.message : null}
                >
                  <input
                    type="number"
                    min="1"
                    max="16"
                    value=${limits[tool]}
                    onInput=${(/** @type {Event} */ ev) =>
                      setDraft({ ...limits, [tool]: /** @type {HTMLInputElement} */ (ev.currentTarget).value })}
                  />
                <//>`,
              )}
            </div>
            <div><button class="btn btn-primary" type="submit" disabled=${saving || !draft}>${saving ? 'Saving…' : 'Save'}</button></div>`}
      </form>
      ${settings.data ? html`<${PlannerSettings} settings=${settings.data} onSaved=${settings.setData} />` : null}
      <section class="panel form">
        <h2 class="panel-title">Appearance</h2>
        <${Field} label="Theme">
          <select value=${theme} onChange=${changeTheme}>
            <option value="dark">Dark</option>
            <option value="light">Light</option>
            <option value="system">System</option>
          </select>
        <//>
      </section>
    </div>
  </section>`;
}
