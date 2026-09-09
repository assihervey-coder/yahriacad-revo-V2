/* CreditDashboard — solde, estimations pré-action, historique [DeepPCB].
 *
 * - Gros chiffre : solde USD (grand livre pay-as-you-go).
 * - Estimations : passe de routage (~0,40 USD), nuit d'optimisation
 *   300 itérations (≈ 1,20 USD), export Gerber (0,05 USD) — chip
 *   abordable/bloqué, boutons désactivés + tooltip si solde insuffisant.
 * - Historique des credit_debit (temps réel via useCredits).
 * - Recharge simulée (+10 / +50 USD).
 */

import React from 'react';
import { useCredits } from './useCredits.js';
import { useProject } from '../../context/index.js';

function formatUsd(value, digits = 2) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return value.toFixed(digits);
}

function formatTime(ts) {
  try {
    return new Date(ts * (ts > 1e12 ? 1 : 1000)).toLocaleTimeString('fr-FR', {
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '—';
  }
}

export default function CreditDashboard() {
  const { projectId } = useProject();
  const { balance, transactions, estimates, loading, demo, error, refresh, canAfford, topUp } =
    useCredits(projectId);

  const cheapest = estimates.length
    ? Math.min(...estimates.map((e) => e.estimated_usd))
    : null;
  const blocked = cheapest !== null && !canAfford(cheapest);

  return (
    <section className="card card--credits" aria-label="Crédits">
      <header className="card__header">
        <span className="card__title">Crédits</span>
        <span className="badge badge--deep">DeepPCB</span>
        {demo ? <span className="badge badge--warn">démo</span> : null}
        <span className="spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => refresh()} data-tip="Rafraîchir (GET /credits)">
          ⟳
        </button>
      </header>

      <div className="card__body">
        {loading && balance === null ? (
          <span className="muted">Chargement du grand livre…</span>
        ) : (
          <>
            <div className="balance">
              <span className="balance__amount">{formatUsd(balance)}</span>
              <span className="balance__unit">USD disponibles</span>
            </div>

            {error ? (
              <span className="muted" style={{ fontSize: 11 }}>
                {error}
              </span>
            ) : null}

            {blocked ? (
              <div className="blocked-banner" role="alert" data-tip="Rechargez pour débloquer les actions">
                ⛔ Solde insuffisant pour la prochaine action coûteuse
              </div>
            ) : null}

            <div className="credit-estimates">
              {estimates.map((estimate) => (
                <div key={estimate.key} className="credit-estimate">
                  <span className="credit-estimate__label">{estimate.label}</span>
                  <span className="credit-estimate__price">≈ {formatUsd(estimate.estimated_usd)} USD</span>
                  <span className={estimate.affordable ? 'afford--ok' : 'afford--no'}>
                    {estimate.affordable ? '✓ OK' : '✗ bloqué'}
                  </span>
                  <button
                    type="button"
                    className="btn btn--sm"
                    disabled={!estimate.affordable}
                    data-tip={estimate.affordable ? undefined : 'Solde insuffisant — rechargez vos crédits'}
                  >
                    Lancer
                  </button>
                </div>
              ))}
            </div>

            <div className="topup-row">
              <span className="field__label">Recharge simulée</span>
              <button type="button" className="btn btn--sm btn--primary" onClick={() => topUp(10)}>
                +10 USD
              </button>
              <button type="button" className="btn btn--sm btn--primary" onClick={() => topUp(50)}>
                +50 USD
              </button>
            </div>

            <div className="col" style={{ gap: 4 }}>
              <span className="field__label">Historique des débits</span>
              <table className="tx-table">
                <thead>
                  <tr>
                    <th>Heure</th>
                    <th>Action</th>
                    <th>Débit</th>
                    <th>Solde</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.slice(0, 8).map((tx) => (
                    <tr key={tx.tx_id}>
                      <td className="muted">{formatTime(tx.ts)}</td>
                      <td>{tx.label}</td>
                      <td className="tx-amount--neg">−{formatUsd(tx.amount_usd, 2)}</td>
                      <td className="muted">{formatUsd(tx.balance_after_usd, 2)}</td>
                    </tr>
                  ))}
                  {!transactions.length ? (
                    <tr>
                      <td colSpan={4} className="muted">
                        Aucun débit enregistré.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
