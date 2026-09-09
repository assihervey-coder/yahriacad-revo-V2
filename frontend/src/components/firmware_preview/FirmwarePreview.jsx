/* FirmwarePreview — aperçu des .h générés (Zephyr/Arduino) [Flux.ai].
 *
 * - Liste des fichiers par framework, coloration syntaxique C maison.
 * - Diff unifié entre deux versions (lignes ajoutées/supprimées colorées).
 * - Badge « synchronisé » piloté par le flux firmware (étape 7 / export_ready).
 */

import React, { useEffect, useMemo, useState } from 'react';
import { useFirmwareStream } from './useFirmwareStream.js';
import { tokenizeCPerLine } from './highlightC.js';
import { diffLines, diffStats } from './diff.js';

function CodeSpan({ token }) {
  return <span className={token.cls}>{token.text}</span>;
}

/** Rendu du code avec numéros de ligne. */
function CodeView({ code }) {
  const lines = useMemo(() => tokenizeCPerLine(code), [code]);
  return (
    <div className="code-view" role="figure" aria-label="Contenu du header">
      {lines.map((tokens, i) => (
        <div key={i} className="code-line">
          <span className="code-lineno">{i + 1}</span>
          <span>
            {tokens.map((token, j) => (
              <CodeSpan key={j} token={token} />
            ))}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Rendu du diff unifié. */
function DiffView({ oldCode, newCode }) {
  const lines = useMemo(() => diffLines(oldCode, newCode), [oldCode, newCode]);
  const stats = useMemo(() => diffStats(lines), [lines]);
  let oldNo = 0;
  let newNo = 0;
  return (
    <>
      <div className="row" style={{ fontSize: 11, padding: '2px 4px' }}>
        <span className="mono" style={{ color: 'var(--ok)' }}>+{stats.added}</span>
        <span className="mono" style={{ color: 'var(--danger)' }}>−{stats.removed}</span>
      </div>
      <div className="code-view" role="figure" aria-label="Diff entre versions">
        {lines.map((line, i) => {
          const cls =
            line.type === 'add' ? 'code-line code-line--add' : line.type === 'del' ? 'code-line code-line--del' : 'code-line';
          if (line.type !== 'add') oldNo += 1;
          if (line.type !== 'del') newNo += 1;
          return (
            <div key={i} className={cls}>
              <span className="code-lineno">{line.type === 'add' ? '' : oldNo}</span>
              <span className="code-lineno" style={{ flex: '0 0 30px' }}>
                {line.type === 'del' ? '' : newNo}
              </span>
              <span style={{ flex: '0 0 12px', color: line.type === 'add' ? 'var(--ok)' : line.type === 'del' ? 'var(--danger)' : 'transparent' }}>
                {line.type === 'add' ? '+' : line.type === 'del' ? '−' : ' '}
              </span>
              <span>{line.text}</span>
            </div>
          );
        })}
      </div>
    </>
  );
}

export default function FirmwarePreview() {
  const {
    files,
    history,
    framework,
    setFramework,
    synced,
    source,
    lastSyncTs,
    error,
    refresh,
  } = useFirmwareStream();

  const [selectedFile, setSelectedFile] = useState(null);
  const [diffMode, setDiffMode] = useState(false);
  const [versionA, setVersionA] = useState(0); // index dans l'historique (la plus ancienne = 0)

  const scoped = files.filter((f) => f.framework === framework);
  const current = scoped.find((f) => f.filename === selectedFile) || scoped[0] || null;
  const versions = current ? history[current.filename] || [] : [];

  /* Sélection par défaut : premier fichier du framework, dernière version. */
  useEffect(() => {
    if (scoped.length && (!current || !scoped.some((f) => f.filename === current.filename))) {
      setSelectedFile(scoped[0].filename);
      setDiffMode(false);
    }
  }, [scoped, current]);

  const latest = versions.length ? versions[versions.length - 1] : null;
  const versionB = latest ? versions.length - 1 : 0;
  const oldVersion = versions[Math.max(0, Math.min(versionA, versions.length - 2))] || versions[0];

  const syncLabel = synced ? 'Synchronisé' : 'En attente — étape 7';

  return (
    <section className="card card--firmware" aria-label="Aperçu firmware">
      <header className="card__header">
        <span className="card__title">Firmware</span>
        <span className="badge badge--flux">Flux.ai</span>
        <span className={`badge ${synced ? 'badge--ok' : 'badge--muted'}`} data-tip={synced ? 'Headers à jour (event export_ready / étape 7)' : undefined}>
          {synced ? '●' : '○'} {syncLabel}
        </span>
        <span className="spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => refresh()} data-tip="Resynchroniser (GET /exports)">
          ⟳
        </button>
      </header>

      <div className="card__body">
        <div className="fw-toolbar">
          <div className="seg-group" role="group" aria-label="Framework">
            <button
              type="button"
              className={`btn btn--sm ${framework === 'zephyr' ? 'btn--active' : ''}`}
              onClick={() => {
                setFramework('zephyr');
                setDiffMode(false);
              }}
            >
              Zephyr
            </button>
            <button
              type="button"
              className={`btn btn--sm ${framework === 'arduino' ? 'btn--active' : ''}`}
              onClick={() => {
                setFramework('arduino');
                setDiffMode(false);
              }}
            >
              Arduino
            </button>
          </div>
          {current && versions.length > 1 ? (
            <button
              type="button"
              className={`btn btn--sm ${diffMode ? 'btn--primary' : ''}`}
              onClick={() => setDiffMode((v) => !v)}
            >
              Diff
            </button>
          ) : null}
          {source === 'demo' ? <span className="badge badge--warn">démo locale</span> : null}
          {lastSyncTs ? (
            <span className="muted mono" style={{ fontSize: 10 }}>
              maj {new Date(lastSyncTs).toLocaleTimeString('fr-FR')}
            </span>
          ) : null}
        </div>

        {error ? (
          <div className="muted" style={{ fontSize: 11 }}>
            {error}
          </div>
        ) : null}

        <div className="fw-files">
          {scoped.map((file) => (
            <button
              key={file.filename}
              type="button"
              className={`fw-file ${current && file.filename === current.filename ? 'fw-file--active' : ''}`}
              onClick={() => {
                setSelectedFile(file.filename);
                setDiffMode(false);
              }}
            >
              {file.filename}
              <span className="fw-file__version">v{file.version}</span>
            </button>
          ))}
          {!scoped.length ? <span className="muted">Aucun header {framework} pour l'instant.</span> : null}
        </div>

        {current && diffMode && versions.length > 1 ? (
          <div className="col" style={{ gap: 4, flex: 1, minHeight: 0 }}>
            <div className="row" style={{ fontSize: 11 }}>
              <label className="muted" htmlFor="fw-version-a">
                Comparer v
              </label>
              <select
                id="fw-version-a"
                className="input"
                style={{ width: 90, padding: '3px 6px' }}
                value={Math.min(versionA, versions.length - 2)}
                onChange={(e) => setVersionA(Number(e.target.value))}
              >
                {versions.slice(0, -1).map((v, i) => (
                  <option key={v.version} value={i}>
                    v{v.version}
                  </option>
                ))}
              </select>
              <span className="muted">→ v{versions[versionB] ? versions[versionB].version : '—'}</span>
            </div>
            <DiffView oldCode={oldVersion ? oldVersion.code : ''} newCode={latest ? latest.code : ''} />
          </div>
        ) : current ? (
          <div className="col" style={{ gap: 4, flex: 1, minHeight: 0 }}>
            <div className="row" style={{ fontSize: 11 }}>
              <span className="mono muted">{current.filename}</span>
              <span className="badge badge--muted">v{current.version}</span>
            </div>
            <CodeView code={current.code} />
          </div>
        ) : null}
      </div>
    </section>
  );
}
