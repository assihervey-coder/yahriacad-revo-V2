/* Viewer3D — visualisation WebGL de la carte (Three.js).
 *
 * - Charge l'état du design via GET /design/{id}/state (ProjectContext).
 * - Applique les événements temps réel (net_routed, component_moved, …).
 * - HUD : couches toggleables, fps, score DRC, latence WebSocket, presets caméra.
 * - Expose une API impérative (sélection raycast, presets, projection bbox,
 *   aperçu de transformation) consommée par l'éditeur chirurgical via ref.
 */

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { ViewerRenderer } from './renderer.js';
import { COPPER_LAYERS } from './layerManager.js';
import { buildDemoBoard } from './demoBoard.js';
import { useLiveEvents } from '../../services/websocket_live/index.js';
import { api } from '../../services/api.js';
import { useProject } from '../../context/index.js';

/** Correspondance HUD ↔ clés de groupes LayerManager. */
const LAYER_TOGGLES = [
  ...COPPER_LAYERS.map((l) => ({ key: String(l.index), label: l.key, css: l.css })),
  { key: 'vias', label: 'Vias', css: '#E3B341' },
  { key: 'components', label: 'Composants', css: '#8B949E' },
];

const VIEW_PRESETS = [
  { key: 'top', label: 'Dessus' },
  { key: 'iso', label: 'Iso' },
  { key: 'side', label: 'Face' },
];

const Viewer3D = forwardRef(function Viewer3D({ viewerApi }, ref) {
  const canvasRef = useRef(null);
  const rendererRef = useRef(null);
  const pickHandlerRef = useRef(null);
  const drcHandlerRef = useRef(null);
  const loadDemoRef = useRef(null);
  const pickForwardRef = useRef(null);

  const [hud, setHud] = useState({ fps: 0, drcScore: null, nets: '—', vias: 0 });
  const [layerState, setLayerState] = useState(() =>
    Object.fromEntries(LAYER_TOGGLES.map((l) => [l.key, true]))
  );
  const [loadState, setLoadState] = useState({ status: 'vide' });
  const [selInfo, setSelInfo] = useState(null);
  const [notice, setNotice] = useState('');
  const [reloadToken, setReloadToken] = useState(0);

  const { projectId, projectName, designVersion, setDesignVersion, applyMetrics, updateFromEvent } =
    useProject();
  const { lastEvent, connected, status, latencyMs } = useLiveEvents(projectId);

  /* Création / destruction du moteur de rendu (une seule fois). */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const renderer = new ViewerRenderer(canvas, {
      onStats: ({ fps }) => setHud((h) => ({ ...h, fps })),
      onPick: (info) => pickForwardRef.current && pickForwardRef.current(info),
      onDrc: (payload) => drcHandlerRef.current && drcHandlerRef.current(payload),
      onResync: () => setNotice('État resynchronisé par delta depuis le state_manager'),
    });
    rendererRef.current = renderer;
    return () => {
      renderer.dispose();
      rendererRef.current = null;
    };
  }, []);

  /* Handlers vivants (recaptés à chaque rendu, sans recréer le renderer). */
  pickForwardRef.current = (info) => {
    setSelInfo(info && info.ref ? info : null);
    if (typeof pickHandlerRef.current === 'function') pickHandlerRef.current(info);
  };
  drcHandlerRef.current = (payload) => {
    if (payload && typeof payload.score === 'number') {
      setHud((h) => ({ ...h, drcScore: payload.score }));
      applyMetrics({ drcScore: payload.score });
    }
  };

  /* Notification auto-masquée. */
  useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNotice(''), 4500);
    return () => clearTimeout(timer);
  }, [notice]);

  /* Chargement du design : GET /design/{id}/state. */
  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return undefined;
    let cancelled = false;
    if (!projectId) {
      setLoadState({ status: 'vide' });
      return undefined;
    }
    setLoadState({ status: 'chargement' });
    api
      .getDesignState(projectId)
      .then((data) => {
        if (cancelled) return;
        renderer.buildBoardScene(data);
        setLoadState({ status: 'prêt' });
        const meta = renderer.boardMeta;
        setHud((h) => ({
          ...h,
          nets: `${meta.netsRouted}/${meta.netsTotal}`,
          vias: meta.viaCount,
          drcScore: h.drcScore,
        }));
        setDesignVersion(Number(data && data.version) || 0);
        applyMetrics({
          totalNets: meta.netsTotal,
          routedNets: meta.netsRouted,
          viaCount: meta.viaCount,
        });
      })
      .catch((err) => {
        if (!cancelled) setLoadState({ status: 'erreur', message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, reloadToken, setDesignVersion, applyMetrics]);

  /* Événements temps réel → rendu incrémental + métriques globales. */
  useEffect(() => {
    if (!lastEventGuard(lastEvent)) return;
    const renderer = rendererRef.current;
    if (renderer) renderer.applyEvent(lastEvent);
    updateFromEvent(lastEvent);
  }, [lastEvent, updateFromEvent]);

  /* Design de démonstration (local, zéro réseau). */
  const loadDemo = () => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    renderer.buildBoardScene(buildDemoBoard());
    setLoadState({ status: 'prêt', demo: true });
    setNotice('Design de démonstration chargé (90 × 60 mm, 4 couches)');
  };
  loadDemoRef.current = loadDemo;

  /* API impérative consommée par les autres composants. */
  const api = useMemo(
    () => ({
      /** Abonne un handler de sélection (raycast). Renvoie la fonction de désabonnement. */
      onSelect(cb) {
        pickHandlerRef.current = cb;
        return () => {
          if (pickHandlerRef.current === cb) pickHandlerRef.current = null;
        };
      },
      clearSelection() {
        if (rendererRef.current) rendererRef.current.selectRef(null);
      },
      selectRef(ref) {
        if (!rendererRef.current) return null;
        rendererRef.current.selectRef(ref);
        return rendererRef.current.bboxOf(ref);
      },
      setPreset(name) {
        if (rendererRef.current) rendererRef.current.controls.setPreset(name);
      },
      toggleLayer(key, visible) {
        if (rendererRef.current) rendererRef.current.layers.setVisible(key, visible);
      },
      projectBBox(bbox) {
        return rendererRef.current
          ? rendererRef.current.projectBBox(bbox)
          : { visible: false, left: 0, top: 0, width: 0, height: 0 };
      },
      previewTransform(ref, transform) {
        return rendererRef.current ? rendererRef.current.previewTransform(ref, transform) : false;
      },
      cancelPreview(ref) {
        if (rendererRef.current) rendererRef.current.cancelPreview(ref);
      },
      commitPreview(ref) {
        if (rendererRef.current) rendererRef.current.commitPreview(ref);
      },
      loadDemo() {
        if (typeof loadDemoRef.current === 'function') loadDemoRef.current();
      },
      isReady() {
        return !!rendererRef.current;
      },
    }),
    []
  );

  useImperativeHandle(ref, () => api, [api]);
  useEffect(() => {
    if (viewerApi) viewerApi.current = api;
    return () => {
      if (viewerApi && viewerApi.current === api) viewerApi.current = null;
    };
  }, [api, viewerApi]);

  const toggleLayer = (key) => {
    setLayerState((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      if (rendererRef.current) rendererRef.current.layers.setVisible(key, next[key]);
      return next;
    });
  };

  const fpsClass = hud.fps >= 50 ? 'ok' : hud.fps >= 25 ? 'warn' : 'bad';
  const latencyClass = latencyMs === null ? 'muted' : latencyMs <= 100 ? 'ok' : 'warn';

  return (
    <section className="stage" aria-label="Vue 3D de la carte">
      <canvas ref={canvasRef} className="stage__canvas" />

      {/* HUD — couches */}
      <div className="hud hud--tl" aria-label="Couches">
        <div className="hud__title">Couches</div>
        {LAYER_TOGGLES.map((layer) => (
          <label key={layer.key} className="hud__row hud__row--click">
            <input
              type="checkbox"
              checked={layerState[layer.key]}
              onChange={() => toggleLayer(layer.key)}
            />
            <span className="layer-dot" style={{ background: layer.css }} />
            <span>{layer.label}</span>
          </label>
        ))}
      </div>

      {/* HUD — statistiques */}
      <div className="hud hud--tr" aria-label="Statistiques">
        <div className="hud__stat">
          <span className={`fps--${fpsClass}`}>{hud.fps}</span> fps
        </div>
        <div className="hud__stat">
          Score DRC <strong>{hud.drcScore === null ? '—' : hud.drcScore.toFixed(1)}</strong>
        </div>
        <div className="hud__stat">
          Nets routés <strong>{hud.nets}</strong>
        </div>
        <div className="hud__stat">
          Vias <strong>{hud.vias}</strong>
        </div>
        <div className="hud__stat">
          v{designVersion || 0} ·{' '}
          <span className={`ws-dot ${connected ? 'ws-dot--on' : 'ws-dot--off'}`} />{' '}
          <span className={`latency latency--${latencyClass}`}>
            {latencyMs === null ? (status === 'idle' ? 'ws inactif' : 'ws …') : `${latencyMs} ms`}
          </span>
        </div>
        {loadState.demo ? <span className="badge badge--warn">démo locale</span> : null}
      </div>

      {/* HUD — presets caméra */}
      <div className="hud hud--br" aria-label="Presets de vue">
        {VIEW_PRESETS.map((preset) => (
          <button
            key={preset.key}
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => api.setPreset(preset.key)}
          >
            {preset.label}
          </button>
        ))}
      </div>

      {/* HUD — sélection courante */}
      {selInfo ? (
        <div className="hud hud--bl" aria-label="Sélection">
          <span className="mono">{selInfo.ref}</span>
          <span className="badge badge--muted">{selInfo.block}</span>
          {selInfo.bbox ? (
            <span className="muted">
              {`${(selInfo.bbox.xMax - selInfo.bbox.xMin).toFixed(1)} × ${(
                selInfo.bbox.yMax - selInfo.bbox.yMin
              ).toFixed(1)} mm`}
            </span>
          ) : null}
        </div>
      ) : null}

      {/* état vide / erreur / chargement */}
      {loadState.status !== 'prêt' ? (
        <div className="stage__empty">
          <div className="empty-card">
            {loadState.status === 'chargement' ? (
              <>
                <h3>Chargement du design…</h3>
                <p className="muted">GET /design/{projectId || '…'}/state</p>
              </>
            ) : loadState.status === 'erreur' ? (
              <>
                <h3>Design indisponible</h3>
                <p className="muted">{loadState.message}</p>
                <div className="btn-row">
                  <button type="button" className="btn btn--primary" onClick={() => setReloadToken((t) => t + 1)}>
                    Réessayer
                  </button>
                  <button type="button" className="btn btn--ghost" onClick={() => loadDemoRef.current && loadDemoRef.current()}>
                    Charger la démo
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3>Aucun design chargé</h3>
                <p className="muted">
                  Décrivez votre carte dans l’assistant, ou explorez le design de démonstration
                  (contrôleur de drone STM32 + LoRa).
                </p>
                <button type="button" className="btn btn--primary" onClick={() => loadDemoRef.current && loadDemoRef.current()}>
                  Charger le design de démonstration
                </button>
              </>
            )}
          </div>
        </div>
      ) : null}

      {notice ? <div className="toast toast--in">{notice}</div> : null}
    </section>
  );
});

/* Garde de type : évite d'appliquer un snapshot vide. */
function lastEventGuard(event) {
  return Boolean(event && event.type);
}

export default Viewer3D;
