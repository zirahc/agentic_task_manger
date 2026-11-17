// Shared UI pieces. Never hand-roll close buttons, banners, modal shells or dialogs.
import { html, useState, useEffect, useRef } from '../lib/html.js';
import { exactTime, formatTime, toolInfo } from '../lib/util.js';

/** @param {{onClick: () => void, label?: string, variant?: 'modal'|'card'|'notice'|'ghost'}} p */
export function CloseButton({ onClick, label = 'Close', variant = 'modal' }) {
  return html`<button
    type="button"
    class=${`close-btn close-${variant}`}
    aria-label=${label}
    title=${label}
    onClick=${onClick}
  >
    ×
  </button>`;
}

/**
 * @param {{title: string, onClose: () => void, children?: unknown, footer?: unknown, wide?: boolean}} p
 */
export function Modal({ title, onClose, children, footer, wide = false }) {
  const panel = useRef(/** @type {HTMLDivElement | null} */ (null));
  useEffect(() => {
    const onKey = (/** @type {KeyboardEvent} */ e) => {
      if (e.key === 'Escape') onClose();
    };
    addEventListener('keydown', onKey);
    const first = panel.current?.querySelector('input, textarea, select, button.btn-primary');
    if (first instanceof HTMLElement) first.focus();
    return () => removeEventListener('keydown', onKey);
  }, []);
  return html`<div
    class="modal-backdrop"
    onMouseDown=${(/** @type {MouseEvent} */ e) => {
      if (e.target === e.currentTarget) onClose();
    }}
  >
    <div class=${`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-label=${title} ref=${panel}>
      <header class="modal-head">
        <h2>${title}</h2>
        <${CloseButton} onClick=${onClose} />
      </header>
      <div class="modal-body">${children}</div>
      ${footer ? html`<footer class="modal-foot">${footer}</footer>` : null}
    </div>
  </div>`;
}

/** @param {{variant?: 'info'|'warn'|'error'|'success', children?: unknown, onDismiss?: () => void}} p */
export function Notice({ variant = 'info', children, onDismiss }) {
  return html`<div class=${`notice notice-${variant} ${onDismiss ? 'has-dismiss' : ''}`} role=${variant === 'error' ? 'alert' : 'status'}>
    <div class="notice-text">${children}</div>
    ${onDismiss ? html`<${CloseButton} variant="notice" label="Dismiss" onClick=${onDismiss} />` : null}
  </div>`;
}

const STATUS_LABELS = /** @type {Record<string, string>} */ ({
  'needs-you': 'needs you',
  'awaiting-approval': 'needs you',
});

/** @param {{status: string}} p */
export function StatusPill({ status }) {
  const live = status === 'running' || status === 'planning';
  return html`<span class=${`pill status-${status}`}>
    ${live ? html`<span class="live-dot" aria-hidden="true"></span>` : null}
    ${STATUS_LABELS[status] ?? status}
  </span>`;
}

/** @param {{tool: string}} p */
export function ToolBadge({ tool }) {
  return html`<span class=${`tool-badge tool-${tool}`}>${toolInfo(tool).short}</span>`;
}

/** @param {{iso: string | null | undefined}} p */
export function Time({ iso }) {
  return html`<time title=${exactTime(iso)}>${formatTime(iso)}</time>`;
}

/** @param {{title: string, children?: unknown, action?: unknown}} p */
export function Empty({ title, children, action }) {
  return html`<div class="empty">
    <h3>${title}</h3>
    ${children ? html`<p>${children}</p>` : null} ${action ?? null}
  </div>`;
}

export function Loading() {
  return html`<div class="loading" role="status"><span class="spinner" aria-hidden="true"></span> Loading…</div>`;
}

/** @param {{title: string, subtitle?: string, actions?: unknown}} p */
export function PageHeader({ title, subtitle, actions }) {
  return html`<header class="page-head">
    <div class="page-title">
      <h1>${title}</h1>
      ${subtitle ? html`<span class="subtitle">${subtitle}</span>` : null}
    </div>
    <div class="page-actions">${actions ?? null}</div>
  </header>`;
}

/** @param {{label: string, hint?: string, children?: unknown, error?: string | null}} p */
export function Field({ label, hint, children, error }) {
  return html`<label class=${`field ${error ? 'field-error' : ''}`}>
    <span class="field-label">${label}</span>
    ${children} ${hint ? html`<span class="field-hint">${hint}</span>` : null}
    ${error ? html`<span class="field-msg">${error}</span>` : null}
  </label>`;
}

// ---- toasts ----------------------------------------------------------------------------

/** @type {((t: {id: number, text: string, variant: string}) => void) | null} */
let pushToast = null;
let toastSeq = 0;

/** @param {string} text @param {'success'|'error'|'info'} [variant] */
export function toast(text, variant = 'success') {
  pushToast?.({ id: ++toastSeq, text, variant });
}

export function ToastHost() {
  const [items, setItems] = useState(/** @type {{id: number, text: string, variant: string}[]} */ ([]));
  useEffect(() => {
    pushToast = (t) => {
      setItems((list) => [...list.slice(-3), t]);
      setTimeout(() => setItems((list) => list.filter((x) => x.id !== t.id)), t.variant === 'error' ? 7000 : 3500);
    };
    return () => {
      pushToast = null;
    };
  }, []);
  return html`<div class="toasts" aria-live="polite">
    ${items.map(
      (t) => html`<div key=${t.id} class=${`toast toast-${t.variant}`}>
        <span>${t.text}</span>
        <${CloseButton} variant="ghost" label="Dismiss" onClick=${() => setItems((l) => l.filter((x) => x.id !== t.id))} />
      </div>`,
    )}
  </div>`;
}

// ---- confirm dialog ----------------------------------------------------------------------

/**
 * @typedef {{title: string, message: string, confirmLabel?: string, danger?: boolean}} ConfirmOptions
 * @type {((req: ConfirmOptions & {resolve: (ok: boolean) => void}) => void) | null}
 */
let openConfirm = null;

/** The only way to ask for confirmation (never window.confirm). Resolves false if no host. */
export function confirmDialog(/** @type {ConfirmOptions} */ options) {
  return new Promise((resolve) => {
    if (!openConfirm) {
      resolve(false);
      return;
    }
    openConfirm({ ...options, resolve });
  });
}

export function ConfirmHost() {
  const [req, setReq] = useState(/** @type {(ConfirmOptions & {resolve: (ok: boolean) => void}) | null} */ (null));
  useEffect(() => {
    openConfirm = setReq;
    return () => {
      openConfirm = null;
    };
  }, []);
  if (!req) return null;
  const done = (/** @type {boolean} */ ok) => {
    req.resolve(ok);
    setReq(null);
  };
  return html`<${Modal}
    title=${req.title}
    onClose=${() => done(false)}
    footer=${html`
      <button type="button" class="btn" onClick=${() => done(false)}>Cancel</button>
      <button type="button" class=${`btn ${req.danger ? 'btn-danger' : 'btn-primary'}`} onClick=${() => done(true)}>
        ${req.confirmLabel ?? 'Confirm'}
      </button>
    `}
  >
    <p class="confirm-text">${req.message}</p>
  <//>`;
}
