/**
 * app.js — application entry point and orchestrator.
 *
 * Wires the DOM to the API client, theme manager, toasts, and render helpers.
 * The backend contract (WebSocket message shapes, /api/upload, /api/download)
 * is preserved exactly; this layer only improves presentation and UX.
 */
import { $, $$, icon, escapeHtml, nowTime, clamp, countUp } from './utils.js';
import { ThemeManager } from './theme.js';
import { Toaster } from './toast.js';
import { ApiClient } from './api.js';
import { renderFileList, renderTable, deriveQuickStats } from './ui.js';

const STATUS_ICON = { success: 'check', error: 'x-circle', info: 'info' };
const MAX_LOGS = 200;

class AttendanceApp {
  constructor() {
    this.files = [];
    this.isProcessing = false;
    this.logCount = 0;
    this.tables = null;          // { detailed, highlighted, summary }
    this.fileUrl = null;
    this.fileName = null;
    this.activeTable = 'detailed';

    this.theme = new ThemeManager();
    this.toasts = new Toaster($('#toastRegion'));
    this.api = new ApiClient();

    this.el = this._collectElements();
    this._bindEvents();
    this._wireApi();
    this._initNavObserver();
    this.api.connect();
    this._addLog('Waiting for server connection…', 'info');
  }

  _collectElements() {
    return {
      app: $('#app'),
      // shell
      navToggle: $('#navToggle'), sidebarClose: $('#sidebarClose'), scrim: $('#scrim'),
      sidebarCollapse: $('#sidebarCollapse'), themeToggle: $('#themeToggle'),
      connDot: $('#connDot'), connLabel: $('#connLabel'),
      navItems: $$('[data-nav]'), navReportsBadge: $('#navReportsBadge'),
      // upload
      dropzone: $('#dropzone'), fileInput: $('#fileInput'), fileList: $('#fileList'),
      fileCountBadge: $('#fileCountBadge'),
      processBtn: $('#processBtn'), processLabel: $('#processLabel'), clearBtn: $('#clearBtn'),
      // progress / status
      progress: $('#progress'), progressLabel: $('#progressLabel'),
      progressValue: $('#progressValue'), progressFill: $('#progressFill'),
      progressDetail: $('#progressDetail'),
      statusMsg: $('#statusMsg'), statusMsgText: $('#statusMsgText'),
      // overview
      overviewEmpty: $('#overviewEmpty'), overview: $('#overview'),
      statRecords: $('#statRecords'), statEmployees: $('#statEmployees'),
      statSource: $('#statSource'), statDateRange: $('#statDateRange'),
      segFull: $('#segFull'), segHalf: $('#segHalf'), segAbsent: $('#segAbsent'), segWeekoff: $('#segWeekoff'),
      cntFull: $('#cntFull'), cntHalf: $('#cntHalf'), cntAbsent: $('#cntAbsent'), cntWeekoff: $('#cntWeekoff'),
      qLate: $('#qLate'), qEarly: $('#qEarly'), qMismatch: $('#qMismatch'), qBelow8: $('#qBelow8'),
      // reports
      reportsCard: $('#reportsCard'),
      downloadName: $('#downloadName'), downloadBtn: $('#downloadBtn'),
      downloadProgress: $('#downloadProgress'), downloadFill: $('#downloadFill'), downloadPct: $('#downloadPct'),
      tableTabs: $('#tableTabs'), tableHost: $('#tableHost'), tableFoot: $('#tableFoot'),
      cntDetailed: $('#cntDetailed'), cntHighlighted: $('#cntHighlighted'), cntSummary: $('#cntSummary'),
      // logs
      logs: $('#logs'), logCountBadge: $('#logCount'),
      clearLogsBtn: $('#clearLogsBtn'), toggleLogsBtn: $('#toggleLogsBtn'),
    };
  }

  // ---------------------------------------------------------------- events
  _bindEvents() {
    const e = this.el;

    // Theme
    e.themeToggle.addEventListener('click', () => {
      const t = this.theme.toggle();
      this.toasts.info(`${t === 'dark' ? 'Dark' : 'Light'} mode`, '', 1800);
    });

    // Sidebar collapse (desktop) + mobile drawer
    e.sidebarCollapse.addEventListener('click', () => {
      const collapsed = e.app.getAttribute('data-sidebar') === 'collapsed';
      e.app.setAttribute('data-sidebar', collapsed ? 'expanded' : 'collapsed');
    });
    const openNav = () => { e.app.setAttribute('data-mobile-nav', 'open'); e.navToggle.setAttribute('aria-expanded', 'true'); };
    const closeNav = () => { e.app.setAttribute('data-mobile-nav', 'closed'); e.navToggle.setAttribute('aria-expanded', 'false'); };
    e.navToggle.addEventListener('click', openNav);
    e.sidebarClose.addEventListener('click', closeNav);
    e.scrim.addEventListener('click', closeNav);

    // Nav navigation (smooth scroll + active state)
    e.navItems.forEach((item) => {
      item.addEventListener('click', (ev) => {
        ev.preventDefault();
        const target = document.querySelector(item.getAttribute('href'));
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        this._setActiveNav(item.getAttribute('href'));
        closeNav();
      });
    });

    // Dropzone
    e.dropzone.addEventListener('click', () => e.fileInput.click());
    e.dropzone.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); e.fileInput.click(); }
    });
    e.dropzone.addEventListener('dragover', (ev) => { ev.preventDefault(); e.dropzone.classList.add('is-dragover'); });
    e.dropzone.addEventListener('dragleave', () => e.dropzone.classList.remove('is-dragover'));
    e.dropzone.addEventListener('drop', (ev) => {
      ev.preventDefault();
      e.dropzone.classList.remove('is-dragover');
      this._addFiles(ev.dataTransfer.files);
    });
    e.fileInput.addEventListener('change', (ev) => { this._addFiles(ev.target.files); ev.target.value = ''; });

    // File list removal (delegated)
    e.fileList.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-remove]');
      if (btn) this._removeFile(Number(btn.dataset.remove));
    });

    // Actions
    e.processBtn.addEventListener('click', () => this._process());
    e.clearBtn.addEventListener('click', () => this._clearAll());
    e.downloadBtn.addEventListener('click', () => this._download());

    // Tables
    e.tableTabs.addEventListener('click', (ev) => {
      const tab = ev.target.closest('.tab');
      if (tab) this._switchTable(tab.dataset.table);
    });

    // Logs
    e.clearLogsBtn.addEventListener('click', () => this._clearLogs());
    e.toggleLogsBtn.addEventListener('click', () => this._toggleLogs());
  }

  _wireApi() {
    this.api
      .on('open', () => { this._setConn('online', 'Connected'); this._addLog('Connected to server', 'success'); })
      .on('close', () => { this._setConn('offline', 'Reconnecting…'); })
      .on('log', (d) => this._addLog(d.message, d.log_type || 'info'))
      .on('progress', (d) => this._setProgress(d.progress, d.message))
      .on('complete', (data) => this._handleComplete(data))
      .on('error', (msg) => this._handleError(msg));
  }

  // ---------------------------------------------------------------- files
  _addFiles(fileList) {
    let rejected = 0;
    for (const file of fileList) {
      const ext = (file.name.split('.').pop() || '').toLowerCase();
      if (!['xlsx', 'xls', 'csv'].includes(ext)) { rejected++; continue; }
      if (this.files.some((f) => f.name === file.name && f.size === file.size)) continue;
      this.files.push(file);
    }
    if (rejected) this.toasts.warning('Unsupported file skipped', 'Only .xlsx, .xls and .csv files are accepted.');
    this._renderFiles();
  }

  _removeFile(i) {
    this.files.splice(i, 1);
    this._renderFiles();
  }

  _renderFiles() {
    renderFileList(this.el.fileList, this.files);
    const n = this.files.length;
    this.el.fileCountBadge.textContent = `${n} file${n === 1 ? '' : 's'}`;
    this.el.processBtn.disabled = n === 0 || this.isProcessing;
    this.el.processLabel.textContent = n > 0 ? `Process ${n} File${n === 1 ? '' : 's'}` : 'Process Files';
  }

  _clearAll() {
    this.files = [];
    this._renderFiles();
    this._hideStatus();
    this.el.progress.hidden = true;
    this.el.overview.hidden = true;
    this.el.overviewEmpty.hidden = false;
    this.el.reportsCard.hidden = true;
    this.el.navReportsBadge.hidden = true;
    this.tables = null; this.fileUrl = null; this.fileName = null;
    this._clearLogs();
  }

  // ---------------------------------------------------------------- process
  async _process() {
    if (!this.files.length || this.isProcessing) return;

    this.isProcessing = true;
    this._hideStatus();
    this.el.reportsCard.hidden = true;
    this.el.progress.hidden = false;
    this.el.processBtn.disabled = true;
    this.el.processLabel.textContent = 'Processing…';
    this._resetOverview();
    this._setProgress(8, 'Uploading files…');

    if (!this.api.isOpen) {
      this.toasts.warning('Not connected', 'Real-time updates are unavailable; reconnecting.');
    }

    try {
      this._addLog('Uploading files…', 'info');
      await this.api.upload(this.files);
      this._addLog('Files uploaded — processing started', 'success');
      this._setProgress(20, 'Files uploaded, processing…');
    } catch (err) {
      this._handleError(err.message);
    }
  }

  _handleComplete(data) {
    this.isProcessing = false;
    this._renderFiles();
    this.el.processLabel.textContent = this.files.length
      ? `Process ${this.files.length} File${this.files.length === 1 ? '' : 's'}` : 'Process Files';
    this._setProgress(100, 'Complete!');

    const summary = data.summary || {};
    this.tables = data.tables || null;
    this.fileUrl = data.file_url || null;
    this.fileName = data.file_name || 'attendance_report.xlsx';

    this._renderOverview(summary);
    this._renderReports();

    this._showStatus('success', 'Processing completed successfully.');
    this.toasts.success('Report ready', this.fileName);
    this._addLog('Processing complete', 'success');

    setTimeout(() => this.el.reportsCard.scrollIntoView({ behavior: 'smooth', block: 'start' }), 250);
  }

  _handleError(message) {
    this.isProcessing = false;
    this._renderFiles();
    this.el.progress.hidden = true;
    this.el.processLabel.textContent = 'Process Files';
    this._showStatus('error', message);
    this.toasts.error('Processing failed', message);
    this._addLog(`Error: ${message}`, 'error');
  }

  // ---------------------------------------------------------------- overview
  _resetOverview() {
    ['statRecords', 'statEmployees', 'cntFull', 'cntHalf', 'cntAbsent', 'cntWeekoff',
     'qLate', 'qEarly', 'qMismatch', 'qBelow8'].forEach((k) => { this.el[k].textContent = '0'; });
    this.el.statSource.textContent = '—';
    this.el.statDateRange.textContent = '—';
  }

  _renderOverview(summary) {
    this.el.overviewEmpty.hidden = true;
    this.el.overview.hidden = false;

    countUp(this.el.statRecords, summary.total_records || 0);
    countUp(this.el.statEmployees, summary.employees || 0);
    this.el.statSource.textContent = summary.source_type || '—';

    const range = summary.date_range || {};
    this.el.statDateRange.textContent = range.start
      ? (range.end && range.end !== range.start ? `${range.start} → ${range.end}` : range.start) : '—';

    const att = summary.attendance_summary || {};
    const full = att['Full day'] || 0;
    const half = att['Half day'] || 0;
    const absent = att['Absent'] || 0;
    const weekoff = att['WeekOff'] || att['Week Off'] || 0;
    const total = full + half + absent + weekoff || 1;

    countUp(this.el.cntFull, full);
    countUp(this.el.cntHalf, half);
    countUp(this.el.cntAbsent, absent);
    countUp(this.el.cntWeekoff, weekoff);
    requestAnimationFrame(() => {
      this.el.segFull.style.width = `${(full / total) * 100}%`;
      this.el.segHalf.style.width = `${(half / total) * 100}%`;
      this.el.segAbsent.style.width = `${(absent / total) * 100}%`;
      this.el.segWeekoff.style.width = `${(weekoff / total) * 100}%`;
    });

    const q = deriveQuickStats(this.tables && this.tables.summary);
    countUp(this.el.qLate, q.late);
    countUp(this.el.qEarly, q.early);
    countUp(this.el.qMismatch, q.mismatch);
    countUp(this.el.qBelow8, q.below8);
  }

  // ---------------------------------------------------------------- reports
  _renderReports() {
    if (!this.tables) return;
    this.el.reportsCard.hidden = false;
    this.el.reportsCard.classList.add('reveal');

    const t = this.tables;
    this.el.cntDetailed.textContent = (t.detailed && t.detailed.total) || 0;
    this.el.cntHighlighted.textContent = (t.highlighted && t.highlighted.total) || 0;
    this.el.cntSummary.textContent = (t.summary && t.summary.total) || 0;

    this.el.navReportsBadge.hidden = false;
    this.el.navReportsBadge.textContent = (t.detailed && t.detailed.total) || 0;

    this.el.downloadName.textContent = this.fileName || 'Report ready';
    this.el.downloadBtn.disabled = !this.fileUrl;

    this.activeTable = 'detailed';
    this._switchTable('detailed');
  }

  _switchTable(key) {
    if (!this.tables || !this.tables[key]) return;
    this.activeTable = key;
    $$('.tab', this.el.tableTabs).forEach((tab) => {
      const on = tab.dataset.table === key;
      tab.classList.toggle('is-active', on);
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    const table = this.tables[key];
    renderTable(this.el.tableHost, table);
    const shown = table.rows ? table.rows.length : 0;
    this.el.tableFoot.textContent = `Showing ${shown} of ${table.total} row${table.total === 1 ? '' : 's'}`;
  }

  // ---------------------------------------------------------------- download
  async _download() {
    if (!this.fileUrl) return;
    const { downloadProgress, downloadBtn } = this.el;
    downloadProgress.classList.add('is-active');
    downloadBtn.disabled = true;
    this._setDownloadProgress(0);
    this._addLog('Downloading report…', 'info');

    try {
      const res = await fetch(this.fileUrl);
      if (!res.ok) throw new Error(`Server responded ${res.status}`);
      // Content-Length may be stripped by proxies; X-File-Size is our reliable fallback
      const total = Number(
        res.headers.get('X-File-Size') ||
        res.headers.get('Content-Length') ||
        res.headers.get('x-file-size') ||
        0
      );

      let blob;
      if (res.body && total) {
        const reader = res.body.getReader();
        const chunks = [];
        let received = 0;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
          received += value.length;
          this._setDownloadProgress((received / total) * 100);
        }
        blob = new Blob(chunks);
      } else {
        blob = await res.blob();
        this._setDownloadProgress(100);
      }

      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = this.fileName || 'attendance_report.xlsx';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      this._setDownloadProgress(100);
      this.toasts.success('Download complete', this.fileName);
      this._addLog('Download complete', 'success');
    } catch (err) {
      // Fallback: let the browser handle it directly.
      const a = document.createElement('a');
      a.href = this.fileUrl;
      a.download = this.fileName || 'attendance_report.xlsx';
      document.body.appendChild(a); a.click(); a.remove();
      this.toasts.warning('Download started', 'Streaming progress unavailable.');
      this._addLog(`Download fallback: ${err.message}`, 'warning');
    } finally {
      setTimeout(() => { downloadProgress.classList.remove('is-active'); downloadBtn.disabled = false; }, 600);
    }
  }

  _setDownloadProgress(v) {
    const c = clamp(v, 0, 100);
    this.el.downloadFill.style.width = `${c}%`;
    this.el.downloadPct.textContent = `${Math.round(c)}%`;
  }

  // ---------------------------------------------------------------- progress
  _setProgress(value, message) {
    const c = clamp(Number(value) || 0, 0, 100);
    this.el.progress.hidden = false;
    this.el.progressFill.style.width = `${c}%`;
    this.el.progressValue.textContent = `${Math.round(c)}%`;
    if (message) { this.el.progressLabel.textContent = message; this.el.progressDetail.textContent = message; }
    if (c >= 100) { this.el.progressLabel.textContent = 'Complete'; this.el.progressDetail.textContent = 'Processing complete'; }
  }

  // ---------------------------------------------------------------- status
  _showStatus(type, text) {
    const m = this.el.statusMsg;
    m.className = `status-msg is-shown status-msg--${type}`;
    m.querySelector('svg use').setAttribute('href', `#i-${STATUS_ICON[type] || 'info'}`);
    this.el.statusMsgText.textContent = text;
  }
  _hideStatus() { this.el.statusMsg.className = 'status-msg'; this.el.statusMsgText.textContent = ''; }

  // ---------------------------------------------------------------- logs
  _addLog(message, type = 'info') {
    const line = document.createElement('div');
    line.className = 'log-line';
    line.innerHTML = `<span class="t">${nowTime()}</span><span class="m ${type}">${escapeHtml(message)}</span>`;
    this.el.logs.appendChild(line);
    this.el.logs.scrollTop = this.el.logs.scrollHeight;
    this.logCount++;
    while (this.el.logs.children.length > MAX_LOGS) { this.el.logs.removeChild(this.el.logs.firstChild); }
    this.el.logCountBadge.textContent = `${this.logCount} log${this.logCount === 1 ? '' : 's'}`;
  }
  _clearLogs() {
    this.el.logs.innerHTML = '';
    this.logCount = 0;
    this.el.logCountBadge.textContent = '0 logs';
  }
  _toggleLogs() {
    const collapsed = this.el.logs.classList.toggle('is-collapsed');
    this.el.toggleLogsBtn.textContent = collapsed ? 'Show' : 'Hide';
    this.el.toggleLogsBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }

  // ---------------------------------------------------------------- connection
  _setConn(state, label) {
    this.el.connDot.className = `dot dot--pulse dot--${state === 'online' ? 'online' : 'offline'}`;
    this.el.connLabel.textContent = label;
  }

  // ---------------------------------------------------------------- nav
  _setActiveNav(href) {
    this.el.navItems.forEach((i) => i.classList.toggle('is-active', i.getAttribute('href') === href));
  }
  _initNavObserver() {
    const sections = ['#upload', '#reports', '#activity'].map((s) => $(s)).filter(Boolean);
    if (!('IntersectionObserver' in window) || !sections.length) return;
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) this._setActiveNav(`#${entry.target.id}`);
      });
    }, { rootMargin: '-45% 0px -50% 0px', threshold: 0 });
    sections.forEach((s) => obs.observe(s));
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.app = new AttendanceApp();
  console.info('Attendance Processing System — UI ready');
});
