/**
 * toast.js — lightweight, accessible toast notifications.
 * Usage: const toasts = new Toaster(regionEl); toasts.success('Title', 'msg');
 */
import { icon, escapeHtml } from './utils.js';

const ICONS = { success: 'check', error: 'x-circle', warning: 'alert', info: 'info' };

export class Toaster {
  constructor(region) {
    this.region = region;
  }

  show(type, title, message = '', timeout = 4500) {
    const el = document.createElement('div');
    el.className = `toast toast--${type}`;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    el.innerHTML = `
      <span class="toast__icon">${icon(ICONS[type] || 'info')}</span>
      <div class="toast__body">
        <div class="toast__title">${escapeHtml(title)}</div>
        ${message ? `<div class="toast__msg">${escapeHtml(message)}</div>` : ''}
      </div>
      <button class="toast__close" aria-label="Dismiss">${icon('x')}</button>`;

    const dismiss = () => this._dismiss(el);
    el.querySelector('.toast__close').addEventListener('click', dismiss);
    this.region.appendChild(el);

    if (timeout) {
      const timer = setTimeout(dismiss, timeout);
      el.addEventListener('mouseenter', () => clearTimeout(timer));
    }
    return el;
  }

  _dismiss(el) {
    if (!el || el.classList.contains('is-leaving')) return;
    el.classList.add('is-leaving');
    el.addEventListener('animationend', () => el.remove(), { once: true });
  }

  success(t, m, to) { return this.show('success', t, m, to); }
  error(t, m, to)   { return this.show('error', t, m, to); }
  warning(t, m, to) { return this.show('warning', t, m, to); }
  info(t, m, to)    { return this.show('info', t, m, to); }
}
