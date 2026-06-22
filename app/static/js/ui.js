/**
 * ui.js — pure render helpers. These functions take data + target elements and
 * produce DOM; they hold no application state.
 */
import { icon, escapeHtml, formatFileSize, truncateMiddle, fileIcon, toNumber } from './utils.js';

/** Render the selected-files list (or an empty state). */
export function renderFileList(listEl, files) {
  if (!files.length) {
    listEl.innerHTML = `
      <div class="empty">
        <span class="empty__icon">${icon('folder')}</span>
        <strong>No files selected</strong>
        <span>Choose one or more attendance exports to begin.</span>
      </div>`;
    return;
  }
  listEl.innerHTML = files.map((file, i) => `
    <div class="file-row" style="animation-delay:${i * 40}ms">
      <span class="file-row__icon">${icon(fileIcon(file.name))}</span>
      <span class="file-row__meta">
        <span class="file-row__name" title="${escapeHtml(file.name)}">${escapeHtml(truncateMiddle(file.name))}</span>
        <span class="file-row__size">${formatFileSize(file.size)}</span>
      </span>
      <button class="file-row__remove" data-remove="${i}" aria-label="Remove ${escapeHtml(file.name)}">${icon('x')}</button>
    </div>`).join('');
}

const ATTENDANCE_PILL = {
  'full day': 'full',
  'half day': 'half',
  'absent': 'absent',
  'weekoff': 'weekoff',
  'week off': 'weekoff',
};

/** Build a preview table from a {columns, rows, total} payload. */
export function renderTable(hostEl, table) {
  if (!table || !table.columns || !table.columns.length) {
    hostEl.innerHTML = `<div class="empty" style="padding:var(--sp-6)"><span class="empty__icon">${icon('report')}</span><strong>No rows to preview</strong></div>`;
    return;
  }
  const { columns, rows } = table;
  const attIdx = columns.findIndex((c) => String(c).trim().toLowerCase() === 'attendance');

  const head = `<tr>${columns.map((c) => `<th>${escapeHtml(c)}</th>`).join('')}</tr>`;
  const body = rows.map((row) => {
    const cells = row.map((cell, ci) => {
      if (cell === null || cell === undefined || cell === '') {
        return `<td class="is-empty">—</td>`;
      }
      if (ci === attIdx) {
        const key = String(cell).trim().toLowerCase();
        const variant = ATTENDANCE_PILL[key];
        if (variant) {
          return `<td><span class="cell-pill cell-pill--${variant}">${escapeHtml(cell)}</span></td>`;
        }
      }
      const text = escapeHtml(cell);
      return `<td title="${text}">${text}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');

  hostEl.innerHTML = `<table class="data-table"><thead>${head}</thead><tbody>${body}</tbody></table>`;
  hostEl.scrollTop = 0;
  hostEl.scrollLeft = 0;
}

/**
 * Derive quick-stat totals from the Summary (pivot) preview table, since the
 * backend does not send these aggregates directly. Counts are summed across the
 * rows present in the preview.
 */
export function deriveQuickStats(summaryTable) {
  const result = { late: 0, early: 0, mismatch: 0, below8: 0 };
  if (!summaryTable || !summaryTable.columns) return result;
  const col = (name) => summaryTable.columns.findIndex((c) => String(c).trim() === name);
  const map = {
    late: col('No of late login'),
    early: col('No of Early Logout'),
    mismatch: col('No of Attendance Mismatch'),
    below8: col('No of Worked below 8 hrs'),
  };
  for (const row of summaryTable.rows) {
    for (const key of Object.keys(map)) {
      if (map[key] >= 0) result[key] += toNumber(row[map[key]]);
    }
  }
  Object.keys(result).forEach((k) => { result[k] = Math.round(result[k]); });
  return result;
}
