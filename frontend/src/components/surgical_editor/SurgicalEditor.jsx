/* SurgicalEditor — modifications chirurgicales [Flux.ai], overlay du viewer.
 *
 * 1. Sélection d'un objet via l'API de sélection du Viewer3D (raycast).
 * 2. Transformation en langage simple ou presets (« déplace de 2 mm vers la
 *    gauche », « rotation de 90° »…).
 * 3. POST /edits/scoped → bounding box d'impact projetée en overlay DOM,
 *    nets concernés, Δ de score, aperçu 3D de la transformation.
 * 4. Revue accept/reject avant commit (POST /edits/commit|reject).
 */

import React, { useEffect, useRef, useState } from 'react';
import { useScopedEdit, EDIT_STATES } from './useScopedEdit.js';
import { serializeBBox, bboxDims } from './bbox.js';
import { useProject } from '../../context/index.js';

const PRESETS = [
  { label: '← 2 mm', instruction: 'déplace de 2 mm vers la gauche' },
  { label: '→ 2 mm', instruction: 'déplace de 2 mm vers la droite' },
  { label: '↑ 2 mm', instruction: 'déplace de 2 mm vers le haut' },
  { label: '↓ 2 mm', instruction: 'déplace de 2 mm vers le bas' },
  { label: '↻ 90°', instruction: 'rotation de 90 degrés horaire' },
  { label: '↺ 90°', instruction: 'rotation de 90 degrés anti-horaire' },
];

const STATE_LABELS = {
  [EDIT_STATES.IDLE]: 'inactif',
  [EDIT_STATES.SELECTING]: 'sélection…',
  [EDIT_STATES.PENDING]: 'commande',
  [EDIT_STATES.REVIEW]: 'revue',
};

const STATE_PILL = {
  [EDIT_STATES.IDLE]: '',
  [EDIT_STATES.SELECTING]: 'pill--selecting',
  [EDIT_STATES.PENDING]: 'pill--pending',
  [EDIT_STATES.REVIEW]: 'pill--review',
};

export default function SurgicalEditor({ viewerApi }) {
  const { projectId } = useProject();
  const editor = useScopedEdit({ projectId, viewerApi });
  const [instruction, setInstruction] = useState('');
  const bboxRef = useRef(null);
  const { state, selection, review, busy, error, concurrentMoves } = editor;

  /* Overlay : repositionne la bbox d'impact pendant la revue (caméra mobile). */
  useEffect(() => {
    if (state !== EDIT_STATES.REVIEW || !review || !review.impact.bbox) return undefined;
    let raf = 0;
    const loop = () => {
      const apiRef = viewerApi && viewerApi.current ? viewerApi.current : null;
      if (apiRef && bboxRef.current) {
        const screen = apiRef.projectBBox(review.impact.bbox);
        const el = bboxRef.current;
        if (!screen.visible) {
          el.style.display = 'none';
        } else {
          el.style.display = 'block';
          el.style.left = `${screen.left}px`;
          el.style.top = `${screen.top}px`;
          el.style.width = `${screen.width}px`;
          el.style.height = `${screen.height}px`;
        }
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [state, review, viewerApi]);

  const hideOverlay = () => {
    if (bboxRef.current) bboxRef.current.style.display = 'none';
  };

  const onSimulate = (text) => {
    editor.simulate(text);
  };

  const dims = review && review.impact.bbox ? bboxDims(review.impact.bbox) : null;
  const showReview = state === EDIT_STATES.REVIEW && review;

  return (
    <>
      {/* rectangle d'impact projeté depuis la 3D */}
      <div ref={bboxRef} className="impact-bbox" style={{ display: 'none' }}>
        {review && review.impact.bbox ? (
          <span className="impact-bbox__label">
            impact {dims ? `${dims.w.toFixed(1)} × ${dims.h.toFixed(1)} mm` : ''}
          </span>
        ) : null}
      </div>

      <section className="surgical" aria-label="Éditeur chirurgical">
        <div className="surgical__header">
          <span className="surgical__title">Éditeur chirurgical</span>
          <span className="badge badge--flux">Flux.ai</span>
          <span className={`pill ${STATE_PILL[state] || ''}`}>{STATE_LABELS[state]}</span>
          {selection ? (
            <span className="mono" style={{ fontSize: 11.5 }}>
              cible : <strong>{selection.ref}</strong>
            </span>
          ) : null}
          <span className="spacer" />
          {state !== EDIT_STATES.IDLE ? (
            <button type="button" className="btn btn--ghost btn--sm" onClick={editor.cancel}>
              Annuler
            </button>
          ) : null}
        </div>

        {error ? (
          <div className="blocked-banner" role="alert">
            ⚠ {error}
          </div>
        ) : null}

        {state === EDIT_STATES.IDLE ? (
          <div className="row">
            <span className="muted" style={{ fontSize: 12 }}>
              Sélectionnez un composant dans la vue 3D, puis décrivez la transformation en langage
              naturel — revue avant commit.
            </span>
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={editor.startSelection}
              disabled={!projectId || !(viewerApi && viewerApi.current)}
              data-tip={projectId ? undefined : 'Créez un projet (chat) ou chargez la démo'}
            >
              Sélectionner un objet
            </button>
          </div>
        ) : null}

        {state === EDIT_STATES.SELECTING ? (
          <div className="row">
            <span style={{ fontSize: 12, color: 'var(--accent)' }}>
              Cliquez un composant dans la vue 3D…
            </span>
            <button type="button" className="btn btn--ghost btn--sm" onClick={editor.stopSelection}>
              Stop
            </button>
          </div>
        ) : null}

        {state === EDIT_STATES.PENDING && selection ? (
          <div className="surgical__body">
            <span className="muted" style={{ fontSize: 11.5 }}>
              {selection.block ? `bloc ${selection.block} · ` : ''}
              {selection.bbox ? serializeBBox(selection.bbox) : ''}
            </span>
            <div className="presets-grid">
              {PRESETS.map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  className="preset-btn"
                  disabled={busy}
                  onClick={() => onSimulate(preset.instruction)}
                >
                  {preset.label}
                </button>
              ))}
            </div>
            <form
              className="row"
              style={{ flex: 1, minWidth: 280 }}
              onSubmit={(e) => {
                e.preventDefault();
                onSimulate(instruction);
              }}
            >
              <input
                className="input"
                style={{ flex: 1 }}
                placeholder='ex. « déplace de 2 mm vers la gauche »'
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                disabled={busy}
              />
              <button type="submit" className="btn btn--primary btn--sm" disabled={busy || !instruction.trim()}>
                {busy ? 'Simulation…' : 'Simuler'}
              </button>
            </form>
          </div>
        ) : null}

        {showReview ? (
          <>
            <div className="review-card">
              <div className="review-grid">
                <div className="review-item">
                  <span className="review-item__label">Commande</span>
                  <span className="review-item__value" style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {review.instruction}
                  </span>
                </div>
                <div className="review-item">
                  <span className="review-item__label">Impact</span>
                  <span className="review-item__value">
                    {dims ? `${dims.w.toFixed(1)} × ${dims.h.toFixed(1)} mm` : '—'}
                  </span>
                </div>
                <div className="review-item">
                  <span className="review-item__label">Δ score DRC</span>
                  <span
                    className={`review-item__value ${
                      review.impact.deltaScore === null
                        ? ''
                        : review.impact.deltaScore >= 0
                          ? 'delta--pos'
                          : 'delta--neg'
                    }`}
                  >
                    {review.impact.deltaScore === null
                      ? '—'
                      : `${review.impact.deltaScore >= 0 ? '+' : ''}${review.impact.deltaScore.toFixed(2)}`}
                  </span>
                </div>
                <div className="review-item">
                  <span className="review-item__label">Nets concernés ({review.impact.nets.length})</span>
                  <span className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
                    {review.impact.nets.slice(0, 6).map((net) => (
                      <span key={net} className="net-chip">
                        {net}
                      </span>
                    ))}
                    {review.impact.nets.length === 0 ? <span className="muted">aucun</span> : null}
                  </span>
                </div>
                {concurrentMoves.length ? (
                  <div className="review-item">
                    <span className="review-item__label">Mouvements concurrents</span>
                    <span className="row" style={{ gap: 4 }}>
                      {concurrentMoves.map((ref) => (
                        <span key={ref} className="net-chip" style={{ color: 'var(--warn)', background: 'var(--warn-soft)', borderColor: 'rgba(240,136,62,.4)' }}>
                          {ref}
                        </span>
                      ))}
                    </span>
                  </div>
                ) : null}
              </div>
              <span className="spacer" />
              <div className="btn-row">
                <button
                  type="button"
                  className="btn btn--ok"
                  onClick={() => {
                    hideOverlay();
                    editor.accept();
                  }}
                  disabled={busy || !review.editId}
                  data-tip={review.editId ? undefined : 'édition sans edit_id (mode démo)'}
                >
                  ✓ Accepter
                </button>
                <button
                  type="button"
                  className="btn btn--danger"
                  onClick={() => {
                    hideOverlay();
                    editor.reject();
                  }}
                  disabled={busy}
                >
                  ✕ Rejeter
                </button>
              </div>
            </div>
            {review.impact.warnings && review.impact.warnings.length ? (
              <div className="muted" style={{ fontSize: 11 }}>
                {review.impact.warnings.map((warning) => (
                  <div key={warning}>• {warning}</div>
                ))}
              </div>
            ) : null}
          </>
        ) : null}
      </section>
    </>
  );
}
