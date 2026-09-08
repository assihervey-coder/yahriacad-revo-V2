/* useConversation — machine à états du chat [Flux.ai].
 *
 * - messages[] : rôles user / assistant / system, blocs structurés.
 * - sendMessage(text) : POST /projects puis POST /pipeline/run, gestion
 *   loading/erreurs ; repli « démo » local si la gateway est injoignable.
 * - Souscrit aux événements step_progress pour narrer le pipeline en direct.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../services/api.js';
import { useLiveEvents, EVENT_TYPES, stepLabel } from '../../services/websocket_live/index.js';
import { useProject } from '../../context/index.js';

let _counter = 0;
const nextId = () => `msg-${Date.now().toString(36)}-${(_counter += 1)}`;

/** Prompts d'exemple proposés sous la zone de saisie. */
export const SAMPLE_PROMPTS = [
  'Une carte drone STM32 + LoRa',
  'Un dock USB-C 4 ports',
  'Régulateur 5 V → 3,3 V 2 A avec USB-C',
  'Capteur BME688 sur I2C avec log',
];

function welcomeMessage() {
  return {
    id: nextId(),
    role: 'assistant',
    text:
      'Décrivez votre carte en langage naturel — je génère le plan, le BOM et la netlist SKiDL, puis lance le pipeline complet (8 étapes, suivi en direct).',
    blocks: [],
    ts: Date.now(),
  };
}

/** Transforme la réponse de POST /projects en blocs structurés. */
function buildBlocksFromProject(project) {
  const blocks = [];
  if (Array.isArray(project && project.bom) && project.bom.length) {
    blocks.push({ type: 'bom_table', rows: project.bom });
  }
  if (Array.isArray(project && project.plan) && project.plan.length) {
    blocks.push({ type: 'plan', steps: project.plan });
  }
  if (typeof (project && project.skidl) === 'string' && project.skidl.trim()) {
    blocks.push({ type: 'code', language: 'python', content: project.skidl });
  }
  if (Array.isArray(project && project.erc_errors) && project.erc_errors.length) {
    blocks.push({ type: 'erc_error', errors: project.erc_errors });
  }
  if (project && project.metrics && typeof project.metrics === 'object') {
    blocks.push({ type: 'metrics', metrics: project.metrics });
  }
  return blocks;
}

/** Réponse de démonstration (backend injoignable) — carte drone STM32 + LoRa. */
function demoResponse(text) {
  return {
    text: `Plan généré pour : « ${text} » — (réponse de démonstration, gateway injoignable).`,
    blocks: [
      {
        type: 'plan',
        steps: [
          { index: 1, title: 'Langage naturel → SKiDL', detail: 'Netlist générée par le parser [Circuitron]' },
          { index: 2, title: 'Planification multi-agents', detail: 'Blocs fonctionnels : mcu, rf, power, io [Siemens Fuse]' },
          { index: 3, title: 'Placement & routage RL', detail: 'Passe DeepPCB ~0,40 USD' },
          { index: 4, title: 'Optimisation nocturne', detail: '300 itérations ≈ 1,20 USD [AutoPCB]' },
          { index: 5, title: 'Vérification multi-physique', detail: 'Thermique + SI [Cadence AuraStack]' },
        ],
      },
      {
        type: 'bom_table',
        rows: [
          { ref: 'U1', mpn: 'STM32H743VIT6', footprint: 'LQFP-100 14x14 mm', price_usd: 11.4, qty: 1 },
          { ref: 'U2', mpn: 'SX1276IMLTRT', footprint: 'Module RF HFAN-1.1', price_usd: 4.85, qty: 1 },
          { ref: 'U4', mpn: 'TPS62840DLCR', footprint: 'DFN-8 2x2 mm', price_usd: 1.35, qty: 1 },
          { ref: 'U6', mpn: 'LSM6DSRTR', footprint: 'LGA-14 3x2.5 mm', price_usd: 2.1, qty: 1 },
          { ref: 'J1', mpn: 'USB4105-GF-A', footprint: 'USB-C GCT USB4105', price_usd: 1.2, qty: 1 },
        ],
      },
      {
        type: 'code',
        language: 'python',
        content: [
          'from skidl import *',
          '',
          'mcu = Part("MCU_ST_STM32", "STM32H743VITx", footprint="LQFP-100")',
          'lora = Part("RF_AM_FM", "SX1276", footprint="HFAN-1.1")',
          'usb = Part("Connector", "USB_C_Receptacle", footprint="USB4105")',
          '',
          'usb.DP += mcu.PA12  # USB_DP',
          'usb.DM += mcu.PA11  # USB_DM',
          'lora.SCK += mcu.PA5 # SPI1_SCK',
        ].join('\n'),
      },
    ],
  };
}

export function useConversation() {
  const [messages, setMessages] = useState(() => [welcomeMessage()]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const { projectId, setProjectId, projectName, setProjectName } = useProject();
  const { lastEvent } = useLiveEvents(projectId);
  const lastEventIdRef = useRef(null);
  const messagesEndRef = useRef(null);

  const push = useCallback((msg) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  /** Remplace le dernier message assistant « pending » par le résultat. */
  const replaceLastAssistant = useCallback((patch) => {
    setMessages((prev) => {
      const next = [...prev];
      for (let i = next.length - 1; i >= 0; i -= 1) {
        if (next[i].role === 'assistant') {
          next[i] = { ...next[i], ...patch, id: next[i].id };
          break;
        }
      }
      return next;
    });
  }, []);

  const sendMessage = useCallback(
    async (text) => {
      const clean = String(text || '').trim();
      if (!clean || loading) return;
      setError(null);
      push({ id: nextId(), role: 'user', text: clean, ts: Date.now() });
      push({
        id: nextId(),
        role: 'assistant',
        text: '',
        blocks: [],
        status: 'pending',
        ts: Date.now(),
      });
      setLoading(true);
      try {
        // 1) création du projet — NL → plan/BOM/SKiDL par le cerveau IA
        let project;
        try {
          project = await api.createProject(clean, undefined);
        } catch (err) {
          // Gateway injoignable → réponse de démonstration locale
          const demo = demoResponse(clean);
          replaceLastAssistant({
            text: demo.text,
            blocks: demo.blocks,
            status: 'demo',
            ts: Date.now(),
          });
          setError(
            err instanceof ApiError
              ? err.message
              : 'Gateway injoignable — réponse de démonstration générée localement.'
          );
          return;
        }
        const pid = project && (project.project_id || project.id);
        if (pid) {
          setProjectId(pid);
          setProjectName(project.name || clean.slice(0, 60));
        }
        const blocks = buildBlocksFromProject(project);
        replaceLastAssistant({
          text:
            (project && project.summary) ||
            'Projet créé — voici le plan, le BOM et la netlist SKiDL. Lancement du pipeline…',
          blocks,
          status: 'ok',
          ts: Date.now(),
        });
        // 2) lancement du pipeline 8 étapes (suivi en direct via step_progress)
        if (pid) {
          try {
            await api.runPipeline(pid, { mode: project.mode || 'full' });
            push({
              id: nextId(),
              role: 'system',
              text: 'Pipeline lancé — progression des 8 étapes en direct',
              ts: Date.now(),
            });
          } catch (err) {
            push({
              id: nextId(),
              role: 'system',
              text: `Pipeline non lancé : ${err instanceof ApiError ? err.message : 'erreur réseau'}`,
              ts: Date.now(),
            });
          }
        }
      } finally {
        setLoading(false);
      }
    },
    [loading, push, replaceLastAssistant, setProjectId, setProjectName]
  );

  /* Narration en direct : chaque step_progress devient un message système. */
  useEffect(() => {
    if (!lastEvent || lastEvent.type !== EVENT_TYPES.STEP_PROGRESS) return;
    if (lastEventIdRef.current === lastEvent.event_id) return;
    lastEventIdRef.current = lastEvent.event_id;
    const p = lastEvent.payload || {};
    const step = Number(p.step) || 0;
    const progress = typeof p.progress === 'number' ? Math.round(p.progress * 100) : null;
    const label = stepLabel(step) || p.label || '';
    const suffix = p.status ? ` · ${p.status}` : '';
    push({
      id: nextId(),
      role: 'system',
      text: `Étape ${step}/8 — ${label}${progress !== null ? ` · ${progress} %` : ''}${suffix}`,
      ts: Date.now(),
    });
  }, [lastEvent, push]);

  const reset = useCallback(() => {
    lastEventIdRef.current = null;
    setMessages([welcomeMessage()]);
    setError(null);
  }, []);

  return {
    messages,
    sendMessage,
    loading,
    error,
    reset,
    projectId,
    projectName,
    messagesEndRef,
  };
}
