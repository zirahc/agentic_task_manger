// Time formatting, guarded localStorage, routing and polling helpers.
import { useState, useEffect, useRef, useCallback } from './html.js';

// ---- time ------------------------------------------------------------------------------

/** @param {string | null | undefined} iso */
export function formatTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** @param {string | null | undefined} iso */
export function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
}

/** @param {string | null | undefined} iso */
export function exactTime(iso) {
  return iso ? new Date(iso).toLocaleString() : '';
}

/**
 * @param {string | null | undefined} start
 * @param {string | null | undefined} end
 */
export function duration(start, end) {
  if (!start) return '—';
  const ms = (end ? new Date(end) : new Date()).getTime() - new Date(start).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${String(s)}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${String(m)}m ${String(s % 60)}s`;
  return `${String(Math.floor(m / 60))}h ${String(m % 60)}m`;
}

/** @param {number | undefined} n */
export function formatNumber(n) {
  return typeof n === 'number' ? n.toLocaleString() : '—';
}

// ---- storage ---------------------------------------------------------------------------

/** @param {string} key @param {string} fallback */
export function readLocal(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

/** @param {string} key @param {string} value */
export function writeLocal(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: keep working without it */
  }
}

// ---- routing ---------------------------------------------------------------------------

/** @param {string} path e.g. "/runs/run_123" */
export function navigate(path) {
  location.hash = path;
}

export function useRoute() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const onChange = () => setHash(location.hash);
    addEventListener('hashchange', onChange);
    return () => removeEventListener('hashchange', onChange);
  }, []);
  const parts = hash.replace(/^#\/?/, '').split('/');
  return { view: parts[0] || 'tasks', id: parts[1] ? decodeURIComponent(parts[1]) : null };
}

// ---- data ------------------------------------------------------------------------------

/**
 * Load data once (and on `reload()`); optionally keep polling while `pollMs` is set.
 * @template T
 * @param {() => Promise<T>} load
 * @param {unknown[]} deps
 * @param {number | null} [pollMs]
 */
export function useResource(load, deps, pollMs = null) {
  const [state, setState] = useState(
    /** @type {{data: T | null, error: unknown, loading: boolean}} */ ({
      data: null,
      error: null,
      loading: true,
    }),
  );
  const loadRef = useRef(load);
  loadRef.current = load;
  const seq = useRef(0);

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const data = await loadRef.current();
      if (mine === seq.current) setState({ data, error: null, loading: false });
    } catch (error) {
      if (mine === seq.current) setState((s) => ({ ...s, error, loading: false }));
    }
  }, []);

  useEffect(() => {
    setState((s) => ({ ...s, loading: true }));
    void reload();
  }, deps);

  useEffect(() => {
    if (!pollMs) return undefined;
    const id = setInterval(() => void reload(), pollMs);
    return () => clearInterval(id);
  }, [pollMs, ...deps]);

  return { ...state, reload, setData: (/** @type {T} */ data) => setState((s) => ({ ...s, data })) };
}

export const ACTIVE = ['queued', 'running'];

/** The agent CLIs, in display order (mirror of backend app/tools.py). */
export const TOOLS = [
  { id: 'claude', label: 'Claude Code', short: 'Claude', command: 'claude' },
  { id: 'codex', label: 'Codex', short: 'Codex', command: 'codex' },
  { id: 'antigravity', label: 'Antigravity', short: 'Antigravity', command: 'agy' },
];

/** @param {string} id */
export function toolInfo(id) {
  return TOOLS.find((t) => t.id === id) ?? { id, label: id, short: id, command: id };
}

/** @param {string} status  A session or step status. */
export function isActive(status) {
  return ACTIVE.includes(status);
}

/** @param {string} status  A run status: also live while the planner works. */
export function isRunActive(status) {
  return status === 'planning' || ACTIVE.includes(status);
}
