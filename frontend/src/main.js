import { html, render, useState } from './lib/html.js';
import { api } from './lib/api.js';
import { readLocal, useResource, useRoute, writeLocal } from './lib/util.js';
import { ConfirmHost, ToastHost } from './components/ui.js';
import { TasksView, TaskDetail } from './views/tasks.js';
import { RunsView, RunDetail } from './views/runs.js';
import { SessionsView } from './views/sessions.js';
import { RolesView } from './views/roles.js';
import { ProfilesView } from './views/profiles.js';
import { EnvironmentView, SettingsView } from './views/environment.js';

const NAV = [
  { id: 'tasks', label: 'Tasks', icon: '▦' },
  { id: 'runs', label: 'Runs', icon: '▶' },
  { id: 'sessions', label: 'Sessions', icon: '◉' },
  { id: 'roles', label: 'Roles', icon: '✦' },
  { id: 'profiles', label: 'Profiles', icon: '⚙' },
  { id: 'environment', label: 'Environment', icon: '◍' },
  { id: 'settings', label: 'Settings', icon: '☰' },
];

function App() {
  const route = useRoute();
  const [collapsed, setCollapsed] = useState(readLocal('dash.sidebar', 'open') === 'collapsed');
  // Small indicator poll: how many sessions are live right now.
  const active = useResource(() => api.sessions(true), [], 4000);
  const running = /** @type {any[]} */ (active.data ?? []).filter((s) => s.status === 'running').length;
  const queued = /** @type {any[]} */ (active.data ?? []).length - running;

  const toggle = () => {
    writeLocal('dash.sidebar', collapsed ? 'open' : 'collapsed');
    setCollapsed(!collapsed);
  };

  return html`<div class=${`shell ${collapsed ? 'collapsed' : ''}`}>
    <nav class="sidebar" aria-label="Main">
      <div class="brand">
        <span class="logo" aria-hidden="true">D</span>
        <span class="brand-text">
          <strong>Agent Dashboard</strong>
          <span class="muted small" title="Claude Code · Codex · Antigravity">Local agent CLIs</span>
        </span>
        <button class="icon-btn collapse-btn" aria-label=${collapsed ? 'Expand sidebar' : 'Collapse sidebar'} title=${collapsed ? 'Expand sidebar' : 'Collapse sidebar'} onClick=${toggle}>
          ${collapsed ? '»' : '«'}
        </button>
      </div>
      <ul class="nav">
        ${NAV.map(
          (n) => html`<li key=${n.id}>
            <a href=${`#/${n.id}`} class=${route.view === n.id ? 'active' : ''} aria-current=${route.view === n.id ? 'page' : undefined} title=${n.label}>
              <span class="nav-icon" aria-hidden="true">${n.icon}</span>
              <span class="nav-label">${n.label}</span>
              ${n.id === 'sessions' && running + queued > 0
                ? html`<span class="nav-badge" title=${`${String(running)} running, ${String(queued)} queued`}>${running + queued}</span>`
                : null}
            </a>
          </li>`,
        )}
      </ul>
      <div class="sidebar-foot muted small">No accounts · no telemetry</div>
    </nav>
    <main class="main">${renderView(route)}</main>
    <${ConfirmHost} />
    <${ToastHost} />
  </div>`;
}

/** @param {{view: string, id: string | null}} route */
function renderView(route) {
  switch (route.view) {
    case 'tasks':
      return route.id ? html`<${TaskDetail} key=${route.id} id=${route.id} />` : html`<${TasksView} />`;
    case 'runs':
      return route.id ? html`<${RunDetail} key=${route.id} id=${route.id} />` : html`<${RunsView} />`;
    case 'sessions':
      return html`<${SessionsView} />`;
    case 'roles':
      return html`<${RolesView} />`;
    case 'profiles':
      return html`<${ProfilesView} />`;
    case 'environment':
      return html`<${EnvironmentView} />`;
    case 'settings':
      return html`<${SettingsView} />`;
    default:
      return html`<section class="view"><div class="view-body"><p>Page not found. <a href="#/tasks">Go to Tasks</a></p></div></section>`;
  }
}

const root = document.getElementById('app');
if (root) render(html`<${App} />`, root);
