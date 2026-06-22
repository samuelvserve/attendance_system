/**
 * utils.js — small, dependency-free helpers shared across modules.
 */

/** Query a single element. */
export const $ = (sel, root = document) => root.querySelector(sel);
/** Query all elements as an array. */
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** Build an SVG icon referencing the inline sprite (see index.html <defs>). */
export function icon(name, cls = '') {
  return `<svg ${cls ? `class="${cls}" ` : ''}viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#i-${name}"/></svg>`;
}

/** Escape a value for safe insertion as HTML text. */
export function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Human-readable file size. */
export function formatFileSize(bytes) {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Middle-truncate long file names so the extension stays visible. */
export function truncateMiddle(text, max = 34) {
  if (!text || text.length <= max) return text;
  const keep = Math.floor((max - 1) / 2);
  return `${text.slice(0, keep)}…${text.slice(-keep)}`;
}

/** Pick an icon name for a file based on its extension. */
export function fileIcon(filename) {
  const ext = (filename.split('.').pop() || '').toLowerCase();
  if (ext === 'csv') return 'csv';
  if (ext === 'xlsx' || ext === 'xls') return 'sheet';
  return 'report';
}

/** Clamp a number between min and max. */
export const clamp = (n, min, max) => Math.min(max, Math.max(min, n));

/** Coerce arbitrary cell values to a number (handles "1.5", "", null). */
export function toNumber(v) {
  if (v === null || v === undefined || v === '') return 0;
  const n = typeof v === 'number' ? v : parseFloat(String(v).replace(/[^0-9.\-]/g, ''));
  return Number.isFinite(n) ? n : 0;
}

/** Format the current time as HH:MM:SS for log lines. */
export function nowTime() {
  return new Date().toLocaleTimeString([], { hour12: false });
}

/**
 * Animate an element's text content from 0 to `target` (integers).
 * Respects prefers-reduced-motion by jumping straight to the value.
 */
export function countUp(el, target, duration = 650) {
  if (!el) return;
  const value = Number(target) || 0;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce || value === 0) {
    el.textContent = String(value);
    return;
  }
  const start = performance.now();
  el.classList.add('is-counting');
  const tick = (now) => {
    const p = clamp((now - start) / duration, 0, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = String(Math.round(eased * value));
    if (p < 1) {
      requestAnimationFrame(tick);
    } else {
      el.textContent = String(value);
      el.classList.remove('is-counting');
    }
  };
  requestAnimationFrame(tick);
}
