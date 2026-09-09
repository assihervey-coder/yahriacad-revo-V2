/* MessageBlocks — rendu des blocs structurés renvoyés par le cerveau IA :
 * tableau de composants (BOM), plan de design, code SKiDL coloré, cartes ERC,
 * mini-métriques. Sélection du renderer par bloc.type.
 */

import React, { useMemo } from 'react';

/* Tokenizer « python » minimal pour les blocs SKiDL (bloc mono coloré). */
const PY_KEYWORDS = new Set([
  'import', 'from', 'def', 'return', 'class', 'if', 'else', 'elif', 'for', 'while',
  'with', 'as', 'try', 'except', 'finally', 'raise', 'and', 'or', 'not', 'in',
  'is', 'pass', 'lambda', 'yield', 'global', 'assert', 'True', 'False', 'None',
]);

const PY_MASTER = /(#[^\n]*)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)/g;

export function tokenizePy(code) {
  const out = [];
  let last = 0;
  let match;
  PY_MASTER.lastIndex = 0;
  while ((match = PY_MASTER.exec(String(code || ''))) !== null) {
    if (match.index > last) out.push({ text: String(code).slice(last, match.index), cls: 'tok-id' });
    const [full, comment, str, num, word] = match;
    if (comment) out.push({ text: full, cls: 'tok-comment' });
    else if (str) out.push({ text: full, cls: 'tok-str' });
    else if (num) out.push({ text: full, cls: 'tok-num' });
    else if (word) out.push({ text: full, cls: PY_KEYWORDS.has(word) ? 'tok-kw' : 'tok-id' });
    last = match.index + full.length;
  }
  if (last < String(code || '').length) out.push({ text: String(code).slice(last), cls: 'tok-id' });
  return out;
}

function SkidlCodeBlock({ content, language }) {
  const tokens = useMemo(() => tokenizePy(content), [content]);
  return (
    <pre className="code-block" aria-label={language === 'python' ? 'Code SKiDL' : 'Code'}>
      {tokens.map((token, i) => (
        <span key={i} className={token.cls}>
          {token.text}
        </span>
      ))}
    </pre>
  );
}

function BomTable({ rows }) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return null;
  return (
    <div className="col" style={{ gap: 4 }}>
      <span className="field__label">Nomenclature (BOM)</span>
      <table className="bom-table">
        <thead>
          <tr>
            <th>Réf</th>
            <th>MPN</th>
            <th>Empreinte</th>
            <th>Qté</th>
            <th>Prix u.</th>
          </tr>
        </thead>
        <tbody>
          {list.map((row, i) => (
            <tr key={`${row.ref || i}-${i}`}>
              <td>{row.ref || '—'}</td>
              <td>{row.mpn || row.value || '—'}</td>
              <td>{row.footprint || row.footprint_mm || '—'}</td>
              <td>{row.qty ?? 1}</td>
              <td>{typeof row.price_usd === 'number' ? `${row.price_usd.toFixed(2)} USD` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PlanBlock({ steps }) {
  const list = Array.isArray(steps) ? steps : [];
  if (!list.length) return null;
  return (
    <div className="col" style={{ gap: 4 }}>
      <span className="field__label">Plan de conception</span>
      <ol className="plan-list">
        {list.map((step, i) => (
          <li key={`${step.index ?? i}`} className="plan-step">
            <span className="plan-step__num">{step.index ?? i + 1}</span>
            <span>
              <span className="plan-step__title">{step.title || step.label || '—'}</span>
              {step.detail ? <span className="plan-step__detail"> — {step.detail}</span> : null}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function ErcErrorBlock({ errors }) {
  const list = Array.isArray(errors) ? errors : [];
  if (!list.length) return null;
  return (
    <div className="col" style={{ gap: 6 }}>
      <span className="field__label">Erreurs ERC ({list.length})</span>
      {list.map((err, i) => (
        <div key={`${err.code || i}-${i}`} className="erc-card">
          <span className="erc-card__code">{err.code || 'ERC'}</span>
          <span>
            {err.message || err.msg || 'Violation ERC'}
            {err.ref ? <span className="mono"> ({err.ref})</span> : null}
          </span>
        </div>
      ))}
    </div>
  );
}

function MetricsBlock({ metrics }) {
  const entries = Object.entries(metrics || {}).slice(0, 6);
  if (!entries.length) return null;
  return (
    <div className="metrics-grid">
      {entries.map(([key, value]) => (
        <div key={key} className="metric">
          <div className="metric__value">
            {typeof value === 'number' ? Number(value.toFixed ? value.toFixed(2) : value) : String(value)}
          </div>
          <div className="metric__label">{key.replace(/_/g, ' ')}</div>
        </div>
      ))}
    </div>
  );
}

/** Point d'entrée : rend une liste de blocs. Types inconnus ignorés proprement. */
export default function MessageBlocks({ blocks }) {
  const list = Array.isArray(blocks) ? blocks : [];
  if (!list.length) return null;
  return (
    <div className="col" style={{ gap: 10, width: '100%' }}>
      {list.map((block, i) => {
        if (!block || !block.type) return null;
        switch (block.type) {
          case 'bom_table':
            return <BomTable key={i} rows={block.rows} />;
          case 'plan':
            return <PlanBlock key={i} steps={block.steps} />;
          case 'code':
            return <SkidlCodeBlock key={i} content={block.content} language={block.language} />;
          case 'erc_error':
            return <ErcErrorBlock key={i} errors={block.errors} />;
          case 'metrics':
            return <MetricsBlock key={i} metrics={block.metrics} />;
          default:
            return null;
        }
      })}
    </div>
  );
}
