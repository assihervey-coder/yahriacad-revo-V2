/* Client REST transversal — tout le frontend passe par ici.
 *
 * Base : VITE_API_URL (import.meta.env), fallback http://localhost:8000.
 * Aucun appel au chargement du module : les hooks déclenchent les requêtes.
 */

const RAW_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_API_URL) ||
  'http://localhost:8000';

export const API_URL = String(RAW_BASE).replace(/\/+$/, '');

/** Base pour les WebSocket : http→ws, https→wss. */
export function wsBase() {
  return API_URL.replace(/^http/, 'ws');
}

/** Erreur enrichie du statut HTTP et du payload renvoyé par la gateway. */
export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

async function request(path, { method = 'GET', body, formData, timeoutMs = 20000, signal } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // Propagation d'un signal externe (abort par le composant) si fourni.
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', () => controller.abort(), { once: true });
  }
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method,
      headers: formData ? undefined : body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
      signal: controller.signal,
    });
    if (!res.ok) {
      let payload = null;
      let detail = '';
      try {
        payload = await res.json();
        detail = payload && (payload.detail || payload.message) ? ` — ${payload.detail || payload.message}` : '';
      } catch {
        /* corps non JSON */
      }
      throw new ApiError(`HTTP ${res.status} sur ${path}${detail}`, res.status, payload);
    }
    if (res.status === 204) return null;
    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err && err.name === 'AbortError') throw new ApiError(`Requête ${path} annulée (timeout/abort)`, 0);
    throw new ApiError(`Gateway injoignable (${path}) : ${err && err.message ? err.message : err}`, 0);
  } finally {
    clearTimeout(timer);
  }
}

/* Endpoints contractuels de la gateway (spécification section 11). */
export const api = {
  /** POST /projects — création depuis une description en langage naturel. */
  createProject: (prompt, name) =>
    request('/projects', { method: 'POST', body: { prompt, name: name || undefined } }),

  /** POST /pipeline/run — lance les 8 étapes du workflow sur un projet. */
  runPipeline: (projectId, opts = {}) =>
    request('/pipeline/run', { method: 'POST', body: { project_id: projectId, ...opts } }),

  /** GET /design/{id}/state — état complet versionné (state_manager). */
  getDesignState: (projectId) => request(`/design/${encodeURIComponent(projectId)}/state`),

  /** POST /edits/scoped — modification chirurgicale simulée (avant commit). */
  scopedEdit: (payload) => request('/edits/scoped', { method: 'POST', body: payload }),

  /** POST /edits/commit — applique l'édition simulée. */
  commitEdit: (editId) => request('/edits/commit', { method: 'POST', body: { edit_id: editId } }),

  /** POST /edits/reject — annule l'édition simulée. */
  rejectEdit: (editId) => request('/edits/reject', { method: 'POST', body: { edit_id: editId } }),

  /** GET /credits — solde + historique des transactions (grand livre DeepPCB). */
  getCredits: () => request('/credits'),

  /** GET /credits/estimate?item=…&quantity=… — estimation pré-action. */
  getEstimate: (item, quantity = 1) =>
    request(`/credits/estimate?item=${encodeURIComponent(item)}&quantity=${quantity}`),

  /** POST /credits/topup — recharge simulée (démo). */
  topUp: (amountUsd) => request('/credits/topup', { method: 'POST', body: { amount_usd: amountUsd } }),

  /** POST /files/upload — netlists, datasheets, gerbers d'entrée. */
  uploadFiles: (fileList) => {
    const fd = new FormData();
    for (const file of Array.from(fileList || [])) fd.append('files', file);
    return request('/files/upload', { method: 'POST', formData: fd, timeoutMs: 60000 });
  },

  /** GET /exports/{id} — artefacts générés (Gerber, ODB++, headers firmware). */
  getExports: (projectId) => request(`/exports/${encodeURIComponent(projectId)}`),
};
