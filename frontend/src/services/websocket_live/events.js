/* Nomenclature contractuelle des événements WebSocket (spécification section 11).
 *
 * Chaque événement circulant sur /ws porte :
 *   { event_id, seq, ts, type, project_id, design_version, emitter, payload }
 * Les types ci-dessous SONT le contrat — ne pas renommer.
 */

export const EVENT_TYPES = {
  NET_ROUTED: 'net_routed',
  COMPONENT_MOVED: 'component_moved',
  DRC_UPDATE: 'drc_update',
  STEP_PROGRESS: 'step_progress',
  OPTIMIZER_ITERATION: 'optimizer_iteration',
  EXPORT_READY: 'export_ready',
  CREDIT_DEBIT: 'credit_debit',
  SESSION_RESUMED: 'session_resumed',
  // Événements internes du pipeline multi-agents (section 06)
  PLAN_UPDATED: 'plan_updated',
  BOM_VALIDATED: 'bom_validated',
  SKIDL_GENERATED: 'skidl_generated',
  ERC_ERROR: 'erc_error',
  CONSTRAINT_VIOLATED: 'constraint_violated',
  ROLLBACK_PERFORMED: 'rollback_performed',
  FIRMWARE_REGENERATED: 'firmware_regenerated',
};

/** Descriptions françaises — affichées dans les toasts et le journal live. */
export const EVENT_DESCRIPTIONS = {
  net_routed: 'Une piste a été routée — tracé animé dans le viewer 3D',
  component_moved: 'Un composant a été déplacé — position tweenée en direct',
  drc_update: 'Mise à jour du score DRC/DFM courant',
  step_progress: 'Progression du pipeline (étape sur 8)',
  optimizer_iteration: 'Itération de l’optimiseur nocturne (AutoPCB)',
  export_ready: 'Artefact d’export disponible (Gerber, ODB++, firmware)',
  credit_debit: 'Débit de crédits pay-as-you-go (brique DeepPCB)',
  session_resumed: 'Session restaurée — re-synchronisation par delta depuis le state_manager',
  plan_updated: 'Plan de conception mis à jour par le planner multi-agents',
  bom_validated: 'BOM validé (stocks, prix, empreintes)',
  skidl_generated: 'Netlist SKiDL générée depuis le langage naturel',
  erc_error: 'Erreur ERC détectée sur le schéma',
  constraint_violated: 'Contrainte de conception violée (bus de contraintes)',
  rollback_performed: 'Retour à une version antérieure du design',
  firmware_regenerated: 'Headers firmware régénérés (Zephyr/Arduino)',
};

/** Libellés courts pour l’UI. */
const _PRETTY = {
  net_routed: 'Routage de piste',
  component_moved: 'Déplacement de composant',
  drc_update: 'Score DRC',
  step_progress: 'Étape du pipeline',
  optimizer_iteration: 'Itération optimiseur',
  export_ready: 'Export prêt',
  credit_debit: 'Débit crédits',
  session_resumed: 'Session restaurée',
  plan_updated: 'Plan mis à jour',
  bom_validated: 'BOM validé',
  skidl_generated: 'SKiDL généré',
  erc_error: 'Erreur ERC',
  constraint_violated: 'Contrainte violée',
  rollback_performed: 'Rollback',
  firmware_regenerated: 'Firmware régénéré',
};

/** Les événements qui modifient le design en direct (< 100 ms visés). */
const _LIVE_TYPES = new Set([
  EVENT_TYPES.NET_ROUTED,
  EVENT_TYPES.COMPONENT_MOVED,
  EVENT_TYPES.DRC_UPDATE,
  EVENT_TYPES.OPTIMIZER_ITERATION,
]);

/** true si l’événement anime le design en temps réel (viewer + éditeur). */
export function isLiveEvent(type) {
  return _LIVE_TYPES.has(type);
}

/** Libellé court français d’un type d’événement. */
export function prettyLabel(type) {
  return _PRETTY[type] || (typeof type === 'string' ? type : 'événement');
}

/** Les 8 étapes du workflow (section 09) — labels français + émetteurs. */
export const WORKFLOW_STEPS = [
  { index: 1, label: 'Langage naturel → SKiDL', emitters: 'Flux.ai · Circuitron' },
  { index: 2, label: 'Planification multi-agents', emitters: 'Siemens Fuse · Circuitron' },
  { index: 3, label: 'Placement & routage RL', emitters: 'DeepPCB · Siemens Fuse' },
  { index: 4, label: 'Optimisation autonome de nuit', emitters: 'AutoPCB' },
  { index: 5, label: 'Vérification multi-physique', emitters: 'Cadence AuraStack' },
  { index: 6, label: 'Modifications chirurgicales', emitters: 'Flux.ai' },
  { index: 7, label: 'Génération firmware', emitters: 'Flux.ai' },
  { index: 8, label: 'Export & rétroaction usine', emitters: 'DeepPCB · Siemens Fuse' },
];

/** Libellé français de l’étape n (1..8), chaîne vide si hors bornes. */
export function stepLabel(stepIndex) {
  const found = WORKFLOW_STEPS.find((s) => s.index === Number(stepIndex));
  return found ? found.label : '';
}
