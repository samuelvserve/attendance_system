/**
 * api.js — server communication: WebSocket for real-time progress + REST for
 * uploads. The message/endpoint contract is unchanged from the original app:
 *   WS  /ws            → { type: 'log'|'progress'|'complete'|'error'|'pong', ... }
 *   POST /api/upload   ← FormData: files[], websocket_id, output_format
 *   GET  /api/download/{filename}
 */

export class ApiClient {
  constructor() {
    this.ws = null;
    this.listeners = {};
    this.reconnectDelay = 3000;
    this.pingTimer = null;
    this._closedByUser = false;
    // Stable per-tab ID so the server can route WS messages to the right user.
    // Persists across reconnects within the same tab session.
    this.clientId = `c-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  }

  /** Subscribe to an event: 'open' | 'close' | 'log' | 'progress' | 'complete' | 'error'. */
  on(event, handler) {
    (this.listeners[event] ||= []).push(handler);
    return this;
  }

  _emit(event, payload) {
    (this.listeners[event] || []).forEach((fn) => {
      try { fn(payload); } catch (e) { console.error(`listener error [${event}]`, e); }
    });
  }

  connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/ws?client_id=${encodeURIComponent(this.clientId)}`;
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this._emit('open');
      // Keep the connection warm.
      clearInterval(this.pingTimer);
      this.pingTimer = setInterval(() => this._send({ type: 'ping' }), 25000);
    };

    this.ws.onmessage = (event) => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      switch (data.type) {
        case 'log':      this._emit('log', data); break;
        case 'progress': this._emit('progress', data); break;
        case 'complete': this._emit('complete', data.data || {}); break;
        case 'error':    this._emit('error', data.message || 'Unknown error'); break;
        case 'pong':     break;
        default: /* ignore */ break;
      }
    };

    this.ws.onclose = () => {
      clearInterval(this.pingTimer);
      this._emit('close');
      if (!this._closedByUser) {
        setTimeout(() => this.connect(), this.reconnectDelay);
      }
    };

    this.ws.onerror = () => this._emit('error-socket');
    return this;
  }

  get isOpen() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  _send(obj) {
    if (this.isOpen) {
      try { this.ws.send(JSON.stringify(obj)); } catch { /* noop */ }
    }
  }

  /** Upload selected files for processing. Returns the JSON response. */
  async upload(files, outputFormat = 'excel') {
    const form = new FormData();
    files.forEach((f) => form.append('files', f));
    // Always send our stable clientId so the server routes progress to this tab
    form.append('websocket_id', this.clientId);
    form.append('output_format', outputFormat);

    const res = await fetch('/api/upload', { method: 'POST', body: form });
    if (!res.ok) {
      let detail = 'Upload failed';
      try { detail = (await res.json()).detail || detail; } catch { /* keep default */ }
      throw new Error(detail);
    }
    return res.json();
  }
}
