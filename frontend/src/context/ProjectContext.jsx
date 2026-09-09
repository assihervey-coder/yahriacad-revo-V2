/* Contexte global minimal — ProjectContext.
 *
 * Partagé par tous les composants : project_id courant, version du design,
 * dernière métrique DRC/vias/pipeline reçue en direct.
 */

import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { EVENT_TYPES } from '../services/websocket_live/events.js';

const ProjectContext = createContext(null);

const EMPTY_METRICS = Object.freeze({
  drcScore: null,
  viaCount: 0,
  routedNets: 0,
  totalNets: 0,
  step: 0,
  optimizerIteration: 0,
});

export function ProjectProvider({ children }) {
  const [projectId, setProjectId] = useState(null);
  const [projectName, setProjectName] = useState('');
  const [designVersion, setDesignVersion] = useState(0);
  const [metrics, setMetrics] = useState(EMPTY_METRICS);

  /** Patch partiel et sûr des métriques (ignore les undefined). */
  const applyMetrics = useCallback((patch) => {
    setMetrics((prev) => {
      const next = { ...prev };
      for (const [key, value] of Object.entries(patch || {})) {
        if (value !== undefined) next[key] = value;
      }
      return next;
    });
  }, []);

  /** Réagit aux événements contractuels pour maintenir les métriques globales. */
  const updateFromEvent = useCallback(
    (event) => {
      if (!event) return;
      const version = Number(event.design_version);
      if (version > 0) setDesignVersion((v) => Math.max(v, version));
      const p = event.payload || {};
      switch (event.type) {
        case EVENT_TYPES.DRC_UPDATE:
          applyMetrics({
            drcScore: typeof p.score === 'number' ? p.score : undefined,
            viaCount: typeof p.via_count === 'number' ? p.via_count : undefined,
          });
          break;
        case EVENT_TYPES.STEP_PROGRESS:
          applyMetrics({
            step: Number(p.step) || undefined,
          });
          break;
        case EVENT_TYPES.OPTIMIZER_ITERATION:
          applyMetrics({
            optimizerIteration: Number(p.iteration) || undefined,
            drcScore: typeof p.score === 'number' ? p.score : undefined,
          });
          break;
        default:
          break;
      }
    },
    [applyMetrics]
  );

  const value = useMemo(
    () => ({
      projectId,
      setProjectId,
      projectName,
      setProjectName,
      designVersion,
      setDesignVersion,
      metrics,
      applyMetrics,
      updateFromEvent,
    }),
    [projectId, projectName, designVersion, metrics, applyMetrics, updateFromEvent]
  );

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

/** Accès au contexte — erreur explicite hors provider. */
export function useProject() {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error('useProject doit être utilisé dans <ProjectProvider>');
  return ctx;
}
