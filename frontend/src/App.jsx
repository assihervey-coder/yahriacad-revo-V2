/* App — layout 3 zones de la plateforme.
 *
 * ┌──────────┬──────────────────────────────┬───────────────┐
 * │ Chat     │  Viewer3D (WebGL)            │ Firmware      │
 * │ Crédits  │  + SurgicalEditor (overlay)  │ Pipeline (8)  │
 * └──────────┴──────────────────────────────┴───────────────┘
 *
 * État global minimal via ProjectContext ; socket temps réel partagée via
 * useLiveEvents (une seule connexion WebSocket par projet). La barre haute
 * affiche le projet courant, la version du design et la latence du canal.
 */

import { useEffect, useRef, useState } from 'react';
import { ProjectProvider, useProject } from './context/index.js';
import { Viewer3D } from './components/viewer_3d/index.js';
import { ChatInterface } from './components/chat_interface/index.js';
import { SurgicalEditor } from './components/surgical_editor/index.js';
import { FirmwarePreview } from './components/firmware_preview/index.js';
import { CreditDashboard } from './components/credit_dashboard/index.js';
import { PipelineBar } from './components/pipeline_bar/index.js';
import { useLiveEvents, EVENT_TYPES, prettyLabel, EVENT_DESCRIPTIONS } from './services/websocket_live/index.js';

function Workspace() {
  const { projectId, projectName, designVersion, metrics } = useProject();
  const { lastEvent, connected, latencyMs } = useLiveEvents(projectId);

  /* API impérative du viewer partagée avec l'éditeur chirurgical. */
  const viewerApiRef = useRef(null);

  /* Toasts pour les événements transverses (session restaurée, rollback). */
  const [toast, setToast] = useState(null);
  const seenToastRef = useRef(new Set());
  useEffect(() => {
    if (!lastEvent || !lastEvent.event_id) return;
    if (
      lastEvent.type !== EVENT_TYPES.SESSION_RESUMED &&
      lastEvent.type !== EVENT_TYPES.ROLLBACK_PERFORMED
    ) {
      return;
    }
    if (seenToastRef.current.has(lastEvent.event_id)) return;
    seenToastRef.current.add(lastEvent.event_id);
    setToast({
      id: lastEvent.event_id,
      text: `${prettyLabel(lastEvent.type)} — ${EVENT_DESCRIPTIONS[lastEvent.type] || ''}`,
    });
  }, [lastEvent]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), 4200);
    return () => clearTimeout(timer);
  }, [toast]);

  const latencyClass = latencyMs === null ? 'muted' : latencyMs <= 100 ? 'ok' : 'warn';

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand__logo">⬡</span>
          <span className="brand__name">PCB AI Designer</span>
          <span className="brand__tag">V2</span>
        </div>
        <div className="topbar__meta">
          <span className="badge badge--muted">projet</span>
          <span className="chip mono">{projectId ? projectId : 'aucun'}</span>
          {projectName ? <span className="muted" style={{ fontSize: 12 }}>{projectName}</span> : null}
          <span className="badge badge--muted">design v{designVersion || 0}</span>
          {metrics.drcScore !== null ? (
            <span className="badge badge--ok">DRC {metrics.drcScore.toFixed(1)}</span>
          ) : null}
          <span
            className={`ws-dot ${connected ? 'ws-dot--on' : 'ws-dot--off'}`}
            data-tip={connected ? 'Canal temps réel connecté' : 'Canal temps réel déconnecté'}
          />
          <span className={`latency--${latencyClass} mono`} style={{ fontSize: 11 }}>
            {latencyMs === null ? '— ms' : `${latencyMs} ms`}
          </span>
        </div>
      </header>

      <div className="app-grid">
        <aside className="panel-left">
          <ChatInterface />
          <CreditDashboard />
        </aside>

        <main className="stage-zone" style={{ position: 'relative', minHeight: 0, display: 'flex' }}>
          <div style={{ position: 'relative', flex: 1, minWidth: 0 }}>
            <Viewer3D viewerApi={viewerApiRef} />
            <SurgicalEditor viewerApi={viewerApiRef} />
          </div>
        </main>

        <aside className="panel-right">
          <FirmwarePreview />
          <PipelineBar />
        </aside>
      </div>

      {toast ? <div className="toast toast--in" style={{ position: 'fixed' }}>{toast.text}</div> : null}
    </div>
  );
}

export default function App() {
  return (
    <ProjectProvider>
      <Workspace />
    </ProjectProvider>
  );
}
