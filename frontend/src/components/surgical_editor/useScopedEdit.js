/* useScopedEdit — machine à états de la modification chirurgicale [Flux.ai].
 *
 * idle → selecting (attente d'un clic dans le viewer)
 *      → pending (commande en langage simple / presets)
 *      → review (bbox d'impact + nets concernés + Δ score, aperçu 3D)
 *      → accept (POST /edits/commit) | reject (POST /edits/reject) → idle
 *
 * Intègre websocket_live : les component_moved tombant dans la zone d'impact
 * sont comptabilisés pendant la revue (mouvements concurrents détectés).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../services/api.js';
import { useLiveEvents, EVENT_TYPES } from '../../services/websocket_live/index.js';
import { bboxContains } from './bbox.js';

export const EDIT_STATES = {
  IDLE: 'idle',
  SELECTING: 'selecting',
  PENDING: 'pending',
  REVIEW: 'review',
};

/**
 * @param {object} opts
 * @param {string|null} opts.projectId
 * @param {{ current: object|null }} opts.viewerApi ref partagée vers l'API du Viewer3D
 */
export function useScopedEdit({ projectId, viewerApi }) {
  const [state, setState] = useState(EDIT_STATES.IDLE);
  const [selection, setSelection] = useState(null); // { ref, block, bbox }
  const [review, setReview] = useState(null); // { editId, transform, impact, instruction }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [concurrentMoves, setConcurrentMoves] = useState([]);

  const { lastEvent } = useLiveEvents(projectId);
  const unsubSelectRef = useRef(null);

  const viewer = () => (viewerApi && viewerApi.current ? viewerApi.current : null);

  const clearLocal = useCallback(() => {
    setSelection(null);
    setReview(null);
    setError(null);
    setConcurrentMoves([]);
  }, []);

  /* ---- sélection ---------------------------------------------------------- */

  const startSelection = useCallback(() => {
    const apiRef = viewer();
    if (!apiRef) {
      setError('Viewer 3D indisponible');
      return;
    }
    setError(null);
    setState(EDIT_STATES.SELECTING);
    if (typeof unsubSelectRef.current === 'function') unsubSelectRef.current();
    unsubSelectRef.current = apiRef.onSelect((info) => {
      if (!info || !info.ref) {
        setSelection(null);
        return;
      }
      setSelection(info);
      setState(EDIT_STATES.PENDING);
      if (typeof unsubSelectRef.current === 'function') {
        unsubSelectRef.current();
        unsubSelectRef.current = null;
      }
    });
  }, []);

  const stopSelection = useCallback(() => {
    if (typeof unsubSelectRef.current === 'function') {
      unsubSelectRef.current();
      unsubSelectRef.current = null;
    }
    setState((prev) => (prev === EDIT_STATES.SELECTING ? EDIT_STATES.IDLE : prev));
  }, []);

  /* ---- simulation (POST /edits/scoped) ------------------------------------- */

  const simulate = useCallback(
    async (instruction) => {
      const clean = String(instruction || '').trim();
      if (!selection || !clean || busy) return;
      setBusy(true);
      setError(null);
      try {
        const resp = await api.scopedEdit({
          project_id: projectId,
          ref: selection.ref,
          instruction: clean,
          current_bbox: selection.bbox || null,
        });
        const impact = (resp && resp.impact) || {};
        setReview({
          editId: (resp && (resp.edit_id || resp.editId)) || null,
          transform: (resp && resp.transform) || {},
          impact: {
            bbox: impact.bbox || selection.bbox || null,
            nets: Array.isArray(impact.nets) ? impact.nets : [],
            deltaScore: typeof impact.delta_score === 'number' ? impact.delta_score : null,
            warnings: Array.isArray(impact.warnings) ? impact.warnings : [],
          },
          instruction: clean,
        });
        // aperçu 3D avant commit — l'utilisateur voit le résultat avant d'accepter
        viewer().previewTransform(selection.ref, (resp && resp.transform) || {});
        setState(EDIT_STATES.REVIEW);
      } catch (err) {
        setError(
          err instanceof ApiError ? err.message : 'Simulation impossible (réseau ou viewer absent)'
        );
      } finally {
        setBusy(false);
      }
    },
    [selection, busy, projectId]
  );

  /* ---- revue : accept / reject ---------------------------------------------- */

  const accept = useCallback(async () => {
    if (!review || !review.editId || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.commitEdit(review.editId);
      if (selection) viewer().commitPreview(selection.ref);
      clearLocal();
      setState(EDIT_STATES.IDLE);
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Commit impossible');
      return false;
    } finally {
      setBusy(false);
    }
  }, [review, busy, selection, clearLocal]);

  const reject = useCallback(async () => {
    if (!review || busy) return;
    setBusy(true);
    try {
      if (review.editId) await api.rejectEdit(review.editId);
    } catch {
      /* le rejet local prime : on restaure l'aperçu quoi qu'il arrive */
    } finally {
      if (selection) viewer().cancelPreview(selection.ref);
      clearLocal();
      setState(EDIT_STATES.IDLE);
      setBusy(false);
    }
  }, [review, busy, selection, clearLocal]);

  const cancel = useCallback(() => {
    if (selection && review) viewer().cancelPreview(selection.ref);
    if (typeof unsubSelectRef.current === 'function') {
      unsubSelectRef.current();
      unsubSelectRef.current = null;
    }
    clearLocal();
    setState(EDIT_STATES.IDLE);
  }, [selection, review, clearLocal]);

  /* ---- événements : mouvements concurrents dans la zone d'impact ------------ */

  useEffect(() => {
    if (!lastEvent || lastEvent.type !== EVENT_TYPES.COMPONENT_MOVED) return;
    if (state !== EDIT_STATES.REVIEW || !review || !review.impact.bbox) return;
    const p = lastEvent.payload || {};
    const x = Number(p.x_mm);
    const y = Number(p.y_mm);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    if (bboxContains(review.impact.bbox, x, y)) {
      setConcurrentMoves((prev) => {
        if (prev.includes(p.ref)) return prev;
        return [...prev, p.ref].slice(-8);
      });
    }
  }, [lastEvent, state, review]);

  /* ---- nettoyage ------------------------------------------------------------ */

  useEffect(() => {
    return () => {
      if (typeof unsubSelectRef.current === 'function') unsubSelectRef.current();
    };
  }, []);

  return {
    state,
    selection,
    review,
    busy,
    error,
    concurrentMoves,
    startSelection,
    stopSelection,
    simulate,
    accept,
    reject,
    cancel,
  };
}
