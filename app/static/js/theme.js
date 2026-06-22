/**
 * theme.js — light/dark theme manager. Persists choice in localStorage and
 * falls back to the OS preference. The initial theme is applied pre-paint by
 * an inline script in index.html to avoid a flash of the wrong theme.
 */
const STORAGE_KEY = 'aps-theme';

export class ThemeManager {
  constructor() {
    this.root = document.documentElement;
    this.media = window.matchMedia('(prefers-color-scheme: dark)');
    // Keep in sync with the OS only when the user hasn't made an explicit choice.
    this.media.addEventListener('change', (e) => {
      if (!localStorage.getItem(STORAGE_KEY)) {
        this.apply(e.matches ? 'dark' : 'light');
      }
    });
  }

  get current() {
    return this.root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }

  apply(theme) {
    this.root.setAttribute('data-theme', theme);
  }

  toggle() {
    const next = this.current === 'dark' ? 'light' : 'dark';
    this.apply(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch (e) { /* storage disabled */ }
    return next;
  }
}
