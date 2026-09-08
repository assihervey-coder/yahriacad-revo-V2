/* Store léger partagé — UNE socket WebSocket par projet, quel que soit le
 * nombre de composants abonnés (viewer, chat, éditeur, firmware, crédits).
 *
 * Snapshots immuables consommés par useSyncExternalStore via useLiveEvents.
 */

import { LiveSocket } from './socket.js';

const MAX_EVENTS = 300;

export const IDLE_SNAPSHOT = Object.freeze({
  events: Object.freeze([]),
  lastEvent: null,
  connected: false,
  status: 'idle',
  latencyMs: null,
  resyncCount: 0,
});

class LiveEventStore {
  constructor() {
    /** @type {Map<string, Set<(snap) => void>>} listeners par projectId */
    this._listeners = new Map();
    /** @type {Map<string, object>} snapshots immuables par projectId */
    this._snapshots = new Map();
    /** @type {Map<string, LiveSocket>} une seule socket par projet */
    this._sockets = new Map();
  }

  /** Abonne un callback ; ouvre la socket au premier abonné, la ferme au dernier départ. */
  subscribe(projectId, cb) {
    const pid = projectId || '';
    if (!pid) {
      cb(IDLE_SNAPSHOT);
      return () => {};
    }
    if (!this._listeners.has(pid)) this._listeners.set(pid, new Set());
    const set = this._listeners.get(pid);
    set.add(cb);
    this._ensureSocket(pid);
    cb(this.getSnapshot(pid));
    return () => {
      set.delete(cb);
      if (set.size === 0) {
        this._listeners.delete(pid);
        this._release(pid);
      }
    };
  }

  getSnapshot(projectId) {
    const pid = projectId || '';
    if (!pid) return IDLE_SNAPSHOT;
    let snap = this._snapshots.get(pid);
    if (!snap) {
      snap = IDLE_SNAPSHOT;
      this._snapshots.set(pid, snap);
    }
    return snap;
  }

  /* ---- interne ------------------------------------------------------------ */

  _ensureSocket(pid) {
    if (this._sockets.has(pid)) return;
    const socket = new LiveSocket({
      projectId: pid,
      onEvent: (event) => this._onEvent(pid, event),
      onStatus: (st) => this._onStatus(pid, st),
    });
    this._sockets.set(pid, socket);
    socket.connect();
  }

  _release(pid) {
    const socket = this._sockets.get(pid);
    if (socket) {
      socket.close();
      this._sockets.delete(pid);
    }
    this._snapshots.delete(pid);
  }

  _publish(pid, patch) {
    const prev = this.getSnapshot(pid);
    const next = { ...prev, ...patch };
    this._snapshots.set(pid, next);
    const set = this._listeners.get(pid);
    if (set) {
      for (const cb of [...set]) {
        try {
          cb(next);
        } catch (err) {
          console.error('[liveStore] listener en erreur', err);
        }
      }
    }
  }

  _onEvent(pid, event) {
    const prev = this.getSnapshot(pid);
    const events =
      prev.events.length >= MAX_EVENTS
        ? [...prev.events.slice(prev.events.length - MAX_EVENTS + 1), event]
        : [...prev.events, event];
    this._publish(pid, { events, lastEvent: event });
  }

  _onStatus(pid, st) {
    this._publish(pid, {
      connected: !!st.connected,
      status: st.status,
      latencyMs: st.latencyMs,
      resyncCount: st.resyncCount,
    });
  }
}

export const liveStore = new LiveEventStore();
