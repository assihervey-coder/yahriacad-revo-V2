/* useCredits — comptabilité pay-as-you-go [DeepPCB].
 *
 * - GET /credits (solde + transactions) et GET /credits/estimate?item=…
 *   pour chaque action coûteuse (estimations pré-action).
 * - Souscrit credit_debit en direct : solde et grand livre mis à jour < 100 ms.
 * - Repli « démo » si la gateway est injoignable (grille tarifaire locale
 *   alignée sur common/credits.py : 0,40 / 1,20 / 0,05 USD).
 * - Helpers d'affordability → blocage propre des boutons côté dashboard.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../services/api.js';
import { useLiveEvents, EVENT_TYPES } from '../../services/websocket_live/index.js';

/** Grille tarifaire de secours — miroir de Pricing (common/credits.py). */
const PRICING = [
  { key: 'routing_pass', item: 0.4, label: 'Passe de routage' },
  { key: 'night_optimization', item: 1.2, label: 'Nuit d’optimisation (300 itérations)' },
  { key: 'gerber_export', item: 0.05, label: 'Export Gerber' },
];

const DEMO_BALANCE = 48.35;
const DEMO_TRANSACTIONS = [
  {
    tx_id: 'c4d81f26ab90',
    label: 'Export Gerber',
    amount_usd: 0.05,
    balance_after_usd: 48.35,
    ts: Date.now() - 1000 * 60 * 12,
  },
  {
    tx_id: '2b7e5a90c1d3',
    label: 'Nuit d’optimisation (300 itérations)',
    amount_usd: 1.2,
    balance_after_usd: 48.4,
    ts: Date.now() - 1000 * 60 * 60 * 9,
  },
  {
    tx_id: '8f3a1c9d2e4b',
    label: 'Passe de routage',
    amount_usd: 0.4,
    balance_after_usd: 49.6,
    ts: Date.now() - 1000 * 60 * 60 * 9 - 40000,
  },
];

function labelForItem(item) {
  const found = PRICING.find((p) => Math.abs(p.item - Number(item)) < 1e-9);
  return found ? found.label : `Action ${item}`;
}

function buildEstimates(balance, estimatesFromApi) {
  return PRICING.map((p) => {
    const fromApi = (estimatesFromApi || []).find((e) => Math.abs(Number(e.item) - p.item) < 1e-9);
    const cost = fromApi ? Number(fromApi.estimated_usd) : p.item;
    return {
      key: p.key,
      item: p.item,
      label: (fromApi && fromApi.label) || p.label,
      estimated_usd: cost,
      affordable: Number.isFinite(balance) ? cost <= balance : false,
    };
  });
}

export function useCredits(projectId) {
  const [balance, setBalance] = useState(null);
  const [transactions, setTransactions] = useState([]);
  const [estimates, setEstimates] = useState(() => buildEstimates(null, null));
  const [loading, setLoading] = useState(true);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState(null);

  const { lastEvent } = useLiveEvents(projectId);
  const seenEventsRef = useRef(new Set());

  const loadDemoState = useCallback(() => {
    setBalance(DEMO_BALANCE);
    setTransactions(DEMO_TRANSACTIONS);
    setEstimates(buildEstimates(DEMO_BALANCE, null));
    setDemo(true);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    if (!projectId) {
      loadDemoState();
      setLoading(false);
      return;
    }
    try {
      const data = await api.getCredits();
      const bal = Number(data && data.balance_usd);
      const txs = Array.isArray(data && data.transactions) ? data.transactions : [];
      setBalance(Number.isFinite(bal) ? bal : 0);
      setTransactions(
        txs.map((tx) => ({
          tx_id: tx.tx_id || tx.id || '—',
          label: tx.label || labelForItem(tx.item),
          amount_usd: Number(tx.amount_usd) || 0,
          balance_after_usd: Number(tx.balance_after_usd) || undefined,
          ts: Number(tx.ts) || Date.now() / 1000,
        }))
      );
      setDemo(false);
      // estimations pré-action (tolère une gateway sans cet endpoint)
      try {
        const results = await Promise.all(
          PRICING.map((p) => api.getEstimate(p.item).catch(() => null))
        );
        setEstimates(buildEstimates(bal, results.filter(Boolean)));
      } catch {
        setEstimates(buildEstimates(bal, null));
      }
    } catch (err) {
      loadDemoState();
      setError(
        err instanceof ApiError
          ? `Grand livre indisponible (${err.status || 'réseau'}) — données de démonstration`
          : 'Grand livre indisponible — données de démonstration'
      );
    } finally {
      setLoading(false);
    }
  }, [projectId, loadDemoState]);

  /* Chargement initial + à chaque changement de projet. */
  useEffect(() => {
    refresh();
  }, [refresh]);

  /* Débit en direct — event credit_debit (payload contractuel DeepPCB). */
  useEffect(() => {
    if (!lastEvent || lastEvent.type !== EVENT_TYPES.CREDIT_DEBIT) return;
    if (seenEventsRef.current.has(lastEvent.event_id)) return;
    seenEventsRef.current.add(lastEvent.event_id);
    const p = lastEvent.payload || {};
    if (typeof p.balance_usd === 'number') setBalance(p.balance_usd);
    setTransactions((prev) =>
      [
        {
          tx_id: p.tx_id || 'live',
          label: p.label || labelForItem(p.item),
          amount_usd: Number(p.amount_usd) || 0,
          balance_after_usd: typeof p.balance_usd === 'number' ? p.balance_usd : undefined,
          ts: Number(lastEvent.ts) || Date.now() / 1000,
        },
        ...prev,
      ].slice(0, 50)
    );
    setEstimates((prev) =>
      prev.map((e) => ({
        ...e,
        affordable: typeof p.balance_usd === 'number' ? e.estimated_usd <= p.balance_usd : e.affordable,
      }))
    );
  }, [lastEvent]);

  /** true si le solde couvre le coût (solde inconnu → false, blocage prudent). */
  const canAfford = useCallback(
    (costUsd) => typeof balance === 'number' && balance >= Number(costUsd),
    [balance]
  );

  /** Recharge simulée — optimiste, puis rattrapage backend si disponible. */
  const topUp = useCallback(
    async (amountUsd) => {
      const amount = Number(amountUsd) || 0;
      if (amount <= 0) return;
      setBalance((prev) => (typeof prev === 'number' ? prev + amount : amount));
      setEstimates((prev) =>
        prev.map((e) => ({
          ...e,
          affordable: e.estimated_usd <= (typeof balance === 'number' ? balance + amount : amount),
        }))
      );
      try {
        await api.topUp(amount);
      } catch {
        /* top-up simulé : l'optimisme local suffit en démo */
      }
    },
    [balance]
  );

  return {
    balance,
    transactions,
    estimates,
    loading,
    demo,
    error,
    refresh,
    canAfford,
    topUp,
  };
}
