import { html, useEffect, useState } from '../lib/html.js';
import { api, errorMessage } from '../lib/api.js';
import { navigate, readLocal, useResource, writeLocal } from '../lib/util.js';
import { Empty, Loading, Notice, PageHeader, Time, ToolBadge, confirmDialog, toast } from '../components/ui.js';

const TYPE = 'test-result';
const FENCE = '```';

/** Pasted into a Tester role so its sessions submit results this table understands. */
const TESTER_SNIPPET = [
  'When you finish, report the result as ONE fenced block at the end of your final',
  'answer, exactly in this format (valid JSON, no comments):',
  '',
  `${FENCE}submission`,
  '{',
  '  "type": "test-result",',
  '  "suite": "adblock detection",',
  '  "status": "fail",',
  '  "summary": "One or two sentences: what ran, what failed and why.",',
  '  "commit": "<output of git rev-parse --short HEAD>",',
  '  "counts": {"passed": 12, "failed": 2, "skipped": 0},',
  '  "confusion": {"tp": 8, "fp": 1, "tn": 4, "fn": 1},',
  '  "cases": [',
  '    {"name": "uBlock Origin", "expected": "detected", "actual": "detected", "outcome": "tp"},',
  '    {"name": "Dark Reader", "expected": "clean", "actual": "detected", "outcome": "fp", "note": "why"}',
  '  ]',
  '}',
  FENCE,
  '',
  'Rules:',
  '- "status": pass (everything passed), fail (some tests failed) or error (tests could not run).',
  '- Each case "outcome": tp, fp, tn, fn for detection tests; pass, fail, skip for plain tests.',
  '- Leave out "counts" or "confusion" when they do not apply; they are computed from',
  '  "cases" when missing. List every case you ran, failures first.',
].join('\n');

const RESULT_PILL = /** @type {Record<string, string>} */ ({
  pass: 'status-succeeded',
  fail: 'status-failed',
  error: 'status-paused',
});

const OUTCOME_PILL = /** @type {Record<string, string>} */ ({
  tp: 'status-succeeded',
  tn: 'status-succeeded',
  pass: 'status-succeeded',
  fp: 'status-failed',
  fn: 'status-failed',
  fail: 'status-failed',
  skip: 'status-cancelled',
});

/** Failures first, so the interesting rows are at the top. */
const OUTCOME_ORDER = ['fp', 'fn', 'fail', 'skip', 'tp', 'tn', 'pass'];

/** @param {number | null | undefined} v */
function pct(v) {
  return typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—';
}

/** @param {number | null | undefined} v */
function count(v) {
  return typeof v === 'number' ? String(v) : '—';
}

export function TestsView() {
  const [taskId, setTaskId] = useState(readLocal('dash.tests.task', ''));
  const [openId, setOpenId] = useState(/** @type {string | null} */ (null));
  // Results arrive when a session ends, so only poll while sessions are live.
  const live = useResource(() => api.sessions(true), [], 4000);
  const anyLive = /** @type {any[]} */ (live.data ?? []).length > 0;
  const results = useResource(() => api.submissions(TYPE, taskId), [taskId], anyLive ? 4000 : null);
  const tasks = useResource(() => api.tasks(), []);
  useEffect(() => {
    if (!anyLive) void results.reload(); // catch the result of the session that just ended
  }, [anyLive]);

  const list = /** @type {any[]} */ (results.data ?? []);
  const taskList = /** @type {any[]} */ (tasks.data ?? []);

  const chooseTask = (/** @type {Event} */ e) => {
    const value = /** @type {HTMLSelectElement} */ (e.currentTarget).value;
    writeLocal('dash.tests.task', value);
    setTaskId(value);
  };

  const remove = async (/** @type {any} */ r) => {
    const ok = await confirmDialog({
      title: 'Delete test result',
      message: `Delete this result of "${r.taskTitle}"? The run and its session log are kept.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteSubmission(r.id);
      toast('Result deleted');
      void results.reload();
    } catch (e) {
      toast(errorMessage(e), 'error');
    }
  };

  return html`<section class="view">
    <${PageHeader}
      title="Tests"
      subtitle="Results that Tester sessions submit, newest first."
      actions=${html`<select class="inline-select" value=${taskId} onChange=${chooseTask} aria-label="Filter by task">
        <option value="">All tasks</option>
        ${taskList.map((t) => html`<option key=${t.id} value=${t.id}>${t.title}</option>`)}
      </select>`}
    />
    <div class="view-body">
      ${results.error ? html`<${Notice} variant="error">${errorMessage(results.error)}<//>` : null}
      <${HowTo} />
      ${results.loading && !results.data
        ? html`<${Loading} />`
        : list.length === 0
          ? html`<${Empty} title="No test results yet">
              Add the instructions above to your Tester role, then run it on a task. Each
              ${' '}<code>submission</code> block in its answer appears here when the session ends.
            <//>`
          : html`<div class="table-wrap">
              <table class="table table-click tests-table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Task</th>
                    <th>Suite</th>
                    <th>Commit</th>
                    <th>Result</th>
                    <th class="num" title="passed / failed / skipped">Tests</th>
                    <th class="num" title="True positives">TP</th>
                    <th class="num" title="False positives">FP</th>
                    <th class="num" title="True negatives">TN</th>
                    <th class="num" title="False negatives">FN</th>
                    <th class="num" title="TP / (TP + FP)">Precision</th>
                    <th class="num" title="TP / (TP + FN)">Recall</th>
                    <th class="num">F1</th>
                    <th class="actions-col"><span class="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  ${list.map((r) => {
                    const open = openId === r.id;
                    return [
                      html`<${ResultRow}
                        key=${r.id}
                        r=${r}
                        open=${open}
                        onToggle=${() => setOpenId(open ? null : r.id)}
                        onDelete=${() => void remove(r)}
                      />`,
                      open
                        ? html`<tr key=${`${r.id}-detail`} class="detail-row">
                            <td colspan="14"><${ResultDetail} r=${r} /></td>
                          </tr>`
                        : null,
                    ];
                  })}
                </tbody>
              </table>
            </div>`}
    </div>
  </section>`;
}

/** @param {{r: any, open: boolean, onToggle: () => void, onDelete: () => void}} p */
function ResultRow({ r, open, onToggle, onDelete }) {
  const p = r.payload ?? {};
  const c = r.valid ? p.counts : null;
  const cm = r.valid ? p.confusion : null;
  const m = r.metrics ?? {};
  return html`<tr class=${open ? 'row-open' : ''} onClick=${onToggle} aria-expanded=${open}>
    <td><${Time} iso=${r.createdAt} /></td>
    <td class="ellipsis" title=${r.taskTitle}>${r.taskTitle}</td>
    <td class="ellipsis" title=${p.suite ?? ''}>${r.valid ? (p.suite ?? '—') : '—'}</td>
    <td class="mono">${r.commit ?? p.commit ?? '—'}</td>
    <td>
      ${r.valid
        ? html`<span class=${`pill ${RESULT_PILL[p.status] ?? 'status-cancelled'}`}>${p.status}</span>`
        : html`<span class="pill status-awaiting-approval" title=${r.error}>invalid</span>`}
    </td>
    <td class="num">${c ? `${count(c.passed)} / ${count(c.failed)} / ${count(c.skipped)}` : '—'}</td>
    <td class="num">${count(cm?.tp)}</td>
    <td class=${`num ${cm?.fp ? 'bad' : ''}`}>${count(cm?.fp)}</td>
    <td class="num">${count(cm?.tn)}</td>
    <td class=${`num ${cm?.fn ? 'bad' : ''}`}>${count(cm?.fn)}</td>
    <td class="num">${pct(m.precision)}</td>
    <td class="num">${pct(m.recall)}</td>
    <td class="num"><strong>${pct(m.f1)}</strong></td>
    <td class="actions-col">
      <button
        class="btn btn-ghost btn-sm danger-hover"
        aria-label="Delete result"
        onClick=${(/** @type {Event} */ e) => {
          e.stopPropagation();
          onDelete();
        }}
      >
        Delete
      </button>
    </td>
  </tr>`;
}

/** @param {{r: any}} p */
function ResultDetail({ r }) {
  const p = r.payload ?? {};
  const m = r.metrics ?? {};
  const cases = /** @type {any[]} */ (r.valid ? (p.cases ?? []) : []).slice().sort(
    (a, b) => OUTCOME_ORDER.indexOf(a.outcome) - OUTCOME_ORDER.indexOf(b.outcome),
  );
  return html`<div class="test-detail">
    <div class="test-meta muted small">
      <span><${ToolBadge} tool=${r.tool} /> ${r.roleName}</span>
      <span>session ${r.sessionStatus}</span>
      ${r.commit && p.commit && !r.commit.startsWith(p.commit)
        ? html`<span title="The commit the Tester reported differs from the one checked out">
            reported commit <span class="mono">${p.commit}</span>
          </span>`
        : null}
      <a
        href=${`#/runs/${r.runId}`}
        onClick=${(/** @type {Event} */ e) => {
          e.preventDefault();
          navigate(`/runs/${r.runId}`);
        }}
        >Open run →</a
      >
    </div>
    ${r.valid
      ? null
      : html`<${Notice} variant="warn">
            This block could not be read: ${r.error}
          <//>
          <pre class="code code-scroll">${p.raw ?? ''}</pre>`}
    ${p.summary ? html`<p class="test-summary">${p.summary}</p>` : null}
    ${r.valid && Object.keys(m).length
      ? html`<div class="metric-strip">
          <span><span class="muted">Precision</span> ${pct(m.precision)}</span>
          <span><span class="muted">Recall</span> ${pct(m.recall)}</span>
          <span><span class="muted">F1</span> ${pct(m.f1)}</span>
          <span><span class="muted">Accuracy</span> ${pct(m.accuracy)}</span>
          <span title="FP / (FP + TN)"><span class="muted">False-positive rate</span> ${pct(m.fpr)}</span>
        </div>`
      : null}
    ${cases.length
      ? html`<div class="table-wrap cases-scroll">
          <table class="table">
            <thead>
              <tr>
                <th>Outcome</th>
                <th>Case</th>
                <th>Expected</th>
                <th>Actual</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              ${cases.map(
                (c, i) => html`<tr key=${i}>
                  <td><span class=${`pill ${OUTCOME_PILL[c.outcome] ?? ''}`}>${c.outcome.toUpperCase()}</span></td>
                  <td>${c.name}</td>
                  <td class="muted">${c.expected ?? ''}</td>
                  <td class="muted">${c.actual ?? ''}</td>
                  <td class="muted">${c.note ?? ''}</td>
                </tr>`,
              )}
            </tbody>
          </table>
        </div>`
      : r.valid
        ? html`<p class="muted small">No individual cases were reported.</p>`
        : null}
  </div>`;
}

function HowTo() {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(TESTER_SNIPPET);
      toast('Copied. Paste it at the end of your Tester role instructions.');
    } catch {
      toast('Copy failed. Select the text and copy it manually.', 'error');
    }
  };
  return html`<details class="panel tests-howto">
    <summary>How a Tester submits results</summary>
    <p class="muted small">
      Paste this at the end of your Tester role's instructions (Roles → Tester). When the session ends, the
      dashboard reads the block, checks it, and adds a row here with the checked-out commit.
    </p>
    <pre class="code code-scroll">${TESTER_SNIPPET}</pre>
    <div class="row-actions">
      <button class="btn btn-sm" onClick=${() => void copy()}>Copy instructions</button>
    </div>
  </details>`;
}
