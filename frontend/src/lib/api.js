// The only module that talks to the server. Every request carries `X-Dash: 1`
// (the backend rejects API calls without it, which blocks cross-site requests).

export class ApiError extends Error {
  /**
   * @param {string} code
   * @param {string} message
   * @param {number} status
   * @param {string | undefined} field
   */
  constructor(code, message, status, field) {
    super(message);
    this.code = code;
    this.status = status;
    this.field = field;
  }
}

/**
 * @param {string} method
 * @param {string} path
 * @param {unknown} [body]
 */
async function request(method, path, body) {
  /** @type {Record<string, string>} */
  const headers = { 'X-Dash': '1' };
  /** @type {RequestInit} */
  const init = { method, headers };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError('network', 'Cannot reach the dashboard server. Is it running?', 0, undefined);
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(
      data?.code ?? 'http-error',
      data?.message ?? `Request failed (${String(res.status)}).`,
      res.status,
      data?.field,
    );
  }
  return data;
}

const enc = encodeURIComponent;

export const api = {
  environment: () => request('GET', '/api/environment'),
  settings: () => request('GET', '/api/settings'),
  saveSettings: (/** @type {object} */ s) => request('PUT', '/api/settings', s),

  profiles: () => request('GET', '/api/profiles'),
  createProfile: (/** @type {object} */ p) => request('POST', '/api/profiles', p),
  updateProfile: (/** @type {string} */ id, /** @type {object} */ p) =>
    request('PUT', `/api/profiles/${enc(id)}`, p),
  deleteProfile: (/** @type {string} */ id) => request('DELETE', `/api/profiles/${enc(id)}`),

  roles: () => request('GET', '/api/roles'),
  createRole: (/** @type {object} */ r) => request('POST', '/api/roles', r),
  updateRole: (/** @type {string} */ id, /** @type {object} */ r) =>
    request('PUT', `/api/roles/${enc(id)}`, r),
  deleteRole: (/** @type {string} */ id) => request('DELETE', `/api/roles/${enc(id)}`),

  tasks: () => request('GET', '/api/tasks'),
  task: (/** @type {string} */ id) => request('GET', `/api/tasks/${enc(id)}`),
  createTask: (/** @type {object} */ t) => request('POST', '/api/tasks', t),
  updateTask: (/** @type {string} */ id, /** @type {object} */ t) =>
    request('PUT', `/api/tasks/${enc(id)}`, t),
  moveTask: (/** @type {string} */ id, /** @type {string} */ status) =>
    request('POST', `/api/tasks/${enc(id)}/move`, { status }),
  deleteTask: (/** @type {string} */ id) => request('DELETE', `/api/tasks/${enc(id)}`),
  previewRun: (/** @type {string} */ taskId, /** @type {object} */ body) =>
    request('POST', `/api/tasks/${enc(taskId)}/runs/preview`, body),
  startRun: (/** @type {string} */ taskId, /** @type {object} */ body) =>
    request('POST', `/api/tasks/${enc(taskId)}/runs`, body),

  planTask: (/** @type {string} */ taskId, /** @type {object} */ body) =>
    request('POST', `/api/tasks/${enc(taskId)}/plans`, body),
  approvePlan: (/** @type {string} */ runId) => request('POST', `/api/runs/${enc(runId)}/approve`),
  sendFeedback: (/** @type {string} */ runId, /** @type {string} */ target, /** @type {string} */ text) =>
    request('POST', `/api/runs/${enc(runId)}/feedback`, { target, text }),
  approveStep: (/** @type {string} */ runId, /** @type {string} */ stepId) =>
    request('POST', `/api/runs/${enc(runId)}/steps/${enc(stepId)}/approve`),
  retryStep: (/** @type {string} */ runId, /** @type {string} */ stepId) =>
    request('POST', `/api/runs/${enc(runId)}/steps/${enc(stepId)}/retry`),

  runs: (/** @type {string | undefined} */ taskId) =>
    request('GET', taskId ? `/api/runs?taskId=${enc(taskId)}` : '/api/runs'),
  run: (/** @type {string} */ id) => request('GET', `/api/runs/${enc(id)}`),
  cancelRun: (/** @type {string} */ id) => request('POST', `/api/runs/${enc(id)}/cancel`),
  deleteRun: (/** @type {string} */ id) => request('DELETE', `/api/runs/${enc(id)}`),

  sessions: (/** @type {boolean} */ activeOnly) =>
    request('GET', activeOnly ? '/api/sessions?active=1' : '/api/sessions'),
  sessionEvents: (/** @type {string} */ id, /** @type {number} */ after) =>
    request('GET', `/api/sessions/${enc(id)}/events?after=${String(after)}`),
  cancelSession: (/** @type {string} */ id) => request('POST', `/api/sessions/${enc(id)}/cancel`),
};

/** Human text for any rejection. */
export function errorMessage(/** @type {unknown} */ err) {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
