/* PipelineBar — barre des 8 étapes du workflow, pilotée par step_progress.
 *
 * État par étape : done (numéro < étape courante), active (en cours, avec
 * jauge de progression), todo. Labels français + émetteurs (section 09).
 */

import React, { useEffect, useState } from 'react';
import { useLiveEvents, EVENT_TYPES, WORKFLOW_STEPS } from '../../services/websocket_live/index.js';
import { useProject } from '../../context/index.js';

/** Normalise la progression (0..1 ou 0..100) vers un pourcentage. */
function toPercent(progress) {
  const value = Number(progress) || 0;
  return Math.max(0, Math.min(100, value <= 1 ? value * 100 : value));
}

export default function PipelineBar() {
  const { projectId, metrics } = useProject();
  const { lastEvent, connected } = useLiveEvents(projectId);

  const [currentStep, setCurrentStep] = useState(0);
  const [progress, setProgress] = useState(0);
  const [stepStatus, setStepStatus] = useState('');

  useEffect(() => {
    if (!lastEvent || lastEvent.type !== EVENT_TYPES.STEP_PROGRESS) return;
    const p = lastEvent.payload || {};
    setCurrentStep(Number(p.step) || 0);
    setProgress(toPercent(p.progress));
    setStepStatus(typeof p.status === 'string' ? p.status : '');
  }, [lastEvent]);

  const activeIndex = currentStep || metrics.step || 0;

  return (
    <section className="card card--pipeline" aria-label="Progression du pipeline">
      <header className="card__header">
        <span className="card__title">Pipeline</span>
        <span className="badge badge--muted">8 étapes</span>
        <span className="spacer" />
        <span className={`ws-dot ${connected ? 'ws-dot--on' : 'ws-dot--off'}`} data-tip={connected ? 'WebSocket connectée' : 'WebSocket déconnectée'} />
      </header>

      <div className="pipeline">
        {WORKFLOW_STEPS.map((step) => {
          const done = activeIndex > step.index;
          const active = activeIndex === step.index && activeIndex > 0;
          return (
            <div key={step.index} className={`step ${done ? 'step--done' : ''} ${active ? 'step--active' : ''}`}>
              <span className="step__num">{done ? '✓' : step.index}</span>
              <span className="step__label">
                {step.label}
                <span className="step__emitters"> · {step.emitters}</span>
              </span>
              {active ? (
                <span className="meter" aria-label={`Progression ${Math.round(progress)} %`}>
                  <span className="meter__fill" style={{ width: `${progress}%` }} />
                </span>
              ) : null}
            </div>
          );
        })}
      </div>

      <footer className="pipeline__footer">
        {activeIndex > 0 ? (
          <>
            <span className="mono">étape {activeIndex}/8</span>
            <span className="muted">
              {stepStatusLabel(stepStatus)} · {WORKFLOW_STEPS[activeIndex - 1].label}
            </span>
          </>
        ) : (
          <span className="muted">En attente du lancement du pipeline…</span>
        )}
      </footer>
    </section>
  );
}

function stepStatusLabel(status) {
  const map = {
    started: 'démarrée',
    running: 'en cours',
    done: 'terminée',
    completed: 'terminée',
    failed: 'échec',
    paused: 'en pause',
  };
  return map[status] || (status || 'en cours');
}
