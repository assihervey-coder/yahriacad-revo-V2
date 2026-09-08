/* LiveSocket — connexion WebSocket /ws temps réel (brique DeepPCB).
 *
 * - URL : {VITE_API_URL|http://localhost:8000}/ws?project_id=X&since_seq=Y
 * - Reprise par delta : since_seq = dernier seq reçu, le serveur rejoue.
 * - Détection de gap → re-synchronisation par GET /design/{id}/state.
 * - Reconnexion exponentielle (0,5 s → 15 s) avec jitter.
 * - Buffer circulaire des derniers événements + mesure de latence (echo ping/pong)
 *   contre la cible contractuelle de 100 ms.
 */

import { API_URL, wsBase } from '../api.js';

export const LATENCY_TARGET_MS = 100;
const PING_INTERVAL_MS = 5000;
const MAX_BUFFER = 400;
const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 15000;

export class LiveSocket {
  /**
   * @param {object} opts
   * @param {string} opts.projectId   identifiant de projet (obligatoire)
   * @param {(event: object) => void} [opts.onEvent]  chaque événement contractuel
   * @param {(status: object) => void} [opts.onStatus] changements de statut/latence
   * @param {typeof fetch} [opts.fetchImpl] injectable pour tests
   */
  constructor({ projectId, onEvent, onStatus, fetchImpl } = {}) {
    if (!projectId) throw new Error('[LiveSocket] projectId requis');
    this.projectId = projectId;
    this._onEvent = onEvent || (() => {});
    this._onStatus = onStatus || (() => {});
    this._fetch = fetchImpl || (typeof window !== 'undefined' ? window.fetch.bind(window) : null);

    this._ws = null;
    this._closedByUser = false;
    this._attempt = 0;
    this._reconnectTimer = null;
    this._pingTimer = null;

    this._lastSeq = 0;
    this._buffer = [];
    this._listeners = new Map(); // type → Set<cb> ; '*' = tous les événements
    this._status = 'idle'; // idle | connecting | open | reconnecting | closed
    this._latencyMs = null;
    this._resyncCount = 0;
  }

  /* ---- API publique ------------------------------------------------------ */

  /** Ouvre (ou rouvre) la connexion. Idempotent si déjà ouverte/connectante. */
  connect() {
    if (
      this._ws &&
      (this._ws.readyState === WebSocket.OPEN || this._ws.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    this._closedByUser = false;
    this._setStatus(this._attempt === 0 ? 'connecting' : 'reconnecting');

    // Reprise par delta : on demande le rejouage depuis le dernier seq connu.
    const since = Math.max(0, this._lastSeq);
    const url = `${wsBase()}/ws?project_id=${encodeURIComponent(this.projectId)}&since_seq=${since}`;
    let ws;
    try {
      ws = new WebSocket(url);
    } catch {
      this._scheduleReconnect();
      return;
    }
    this._ws = ws;
    ws.onopen = () => {
      this._attempt = 0;
      this._setStatus('open');
      this._startPing();
    };
    ws.onmessage = (raw) => this._handleMessage(raw);
    ws.onerror = () => {
      // L'événement close suit systématiquement — la reconnexion y est gérée.
    };
    ws.onclose = () => {
      this._stopPing();
      this._ws = null;
      if (this._closedByUser) this._setStatus('closed');
      else this._scheduleReconnect();
    };
  }

  /** Abonne un callback à un type d'événement ('*' = tous). Renvoie la désabonnement. */
  on(type, cb) {
    if (!this._listeners.has(type)) this._listeners.set(type, new Set());
    this._listeners.get(type).add(cb);
    return () => this.off(type, cb);
  }

  /** Désabonne un callback (miroir de on()). */
  off(type, cb) {
    const set = this._listeners.get(type);
    if (set) {
      set.delete(cb);
      if (set.size === 0) this._listeners.delete(type);
    }
  }

  /** Envoie un message JSON si la socket est ouverte. */
  send(data) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(typeof data === 'string' ? data : JSON.stringify(data));
      return true;
    }
    return false;
  }

  /** Fermeture propre : stoppe timers et reconnexion (aucun retry ensuite). */
  close() {
    this._closedByUser = true;
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    this._stopPing();
    if (this._ws) {
      try {
        this._ws.close();
      } catch {
        /* socket déjà fermée */
      }
      this._ws = null;
    }
    this._setStatus('closed');
  }

  get status() {
    return this._status;
  }

  get latencyMs() {
    return this._latencyMs;
  }

  get resyncCount() {
    return this._resyncCount;
  }

  get lastSeq() {
    return this._lastSeq;
  }

  /** Copie du buffer circulaire des derniers événements. */
  getBuffer() {
    return [...this._buffer];
  }

  /* ---- Traitement des messages ------------------------------------------- */

  _handleMessage(raw) {
    let msg;
    try {
      msg = JSON.parse(raw.data);
    } catch {
      return; // trame non JSON — ignorée
    }
    if (msg && msg.type === 'pong') {
      // Echo ts : latence = aller-retour réel du canal.
      if (typeof msg.ts === 'number') {
        this._latencyMs = Math.max(0, Math.round(Date.now() - msg.ts));
        this._notifyStatus();
      }
      return;
    }
    if (!msg || !msg.type || msg.seq === undefined) return; // pas un événement contractuel
    this._ingestEvent(msg);
  }

  _ingestEvent(event) {
    const seq = Number(event.seq) || 0;
    if (this._lastSeq && seq > this._lastSeq + 1) {
      // Trou dans la séquence → re-sync par delta depuis le state_manager.
      this._resyncByDelta(seq);
    }
    if (seq > this._lastSeq) this._lastSeq = seq;

    // Latence mesurée par echo ; sinon estimation horloge (bornée pour l'affichage).
    const estimated =
      this._latencyMs !== null
        ? this._latencyMs
        : Math.min(9999, Math.max(0, Math.round(Date.now() - (Number(event.ts) || 0) * 1000)));
    const stamped = { ...event, _latency_ms: estimated };

    this._buffer.push(stamped);
    if (this._buffer.length > MAX_BUFFER) {
      this._buffer.splice(0, this._buffer.length - MAX_BUFFER);
    }
    this._dispatch(stamped);
    this._onEvent(stamped);
  }

  _dispatch(event) {
    const typed = this._listeners.get(event.type);
    if (typed) for (const cb of [...typed]) this._safe(cb, event);
    const all = this._listeners.get('*');
    if (all) for (const cb of [...all]) this._safe(cb, event);
  }

  _safe(cb, event) {
    try {
      cb(event);
    } catch (err) {
      console.error(`[LiveSocket] listener « ${event.type} » en erreur`, err);
    }
  }

  /* ---- Re-synchronisation par delta -------------------------------------- */

  async _resyncByDelta(targetSeq) {
    this._resyncCount += 1;
    this._notifyStatus();
    try {
      if (!this._fetch) throw new Error('fetch indisponible');
      const res = await this._fetch(
        `${API_URL}/design/${encodeURIComponent(this.projectId)}/state`,
        { headers: { Accept: 'application/json' } }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const state = await res.json();
      this._lastSeq = Math.max(this._lastSeq, targetSeq);
      // Événement synthétique : le viewer reconstruit la scène depuis l'état frais.
      const synthetic = {
        event_id: `resync-${Date.now()}`,
        seq: targetSeq,
        ts: Date.now() / 1000,
        type: 'session_resumed',
        project_id: this.projectId,
        design_version: (state && state.version) || 0,
        emitter: 'websocket_live',
        payload: {
          reason: 'gap_detected',
          from_seq: this._lastSeq,
          to_seq: targetSeq,
          state,
        },
      };
      this._dispatch(synthetic);
      this._onEvent(synthetic);
    } catch (err) {
      // Impossible de resynchroniser : reconnexion franche, le serveur
      // rejouera les événements depuis since_seq.
      console.warn('[LiveSocket] re-sync par delta échouée, reconnexion', err);
      if (this._ws) {
        try {
          this._ws.close();
        } catch {
          /* déjà fermée */
        }
      }
    }
  }

  /* ---- Latence (echo ts) -------------------------------------------------- */

  _startPing() {
    this._stopPing();
    this._pingTimer = setInterval(() => {
      this.send({ type: 'ping', ts: Date.now() });
    }, PING_INTERVAL_MS);
  }

  _stopPing() {
    if (this._pingTimer) {
      clearInterval(this._pingTimer);
      this._pingTimer = null;
    }
  }

  /* ---- Reconnexion exponentielle ------------------------------------------ */

  _scheduleReconnect() {
    this._attempt += 1;
    this._setStatus('reconnecting');
    const delay =
      Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** Math.min(this._attempt, 6)) +
      Math.random() * 250; // jitter anti-thundering herd
    if (this._reconnectTimer) clearTimeout(this._reconnectTimer);
    this._reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  _setStatus(status) {
    if (this._status !== status) {
      this._status = status;
      this._notifyStatus();
    }
  }

  _notifyStatus() {
    this._onStatus({
      status: this._status,
      connected: this._status === 'open',
      latencyMs: this._latencyMs,
      resyncCount: this._resyncCount,
      lastSeq: this._lastSeq,
    });
  }
}
