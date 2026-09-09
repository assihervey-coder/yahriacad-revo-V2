/* Gestionnaire de couches du viewer 3D — visibilité, opacité, couleurs.
 *
 * Couche cuivre (index design_model) → clé three.js :
 *   0 F.Cu (cyan) · 1 GND (violet, plan de masse) · 2 PWR (orange) ·
 *   3 B.Cu (vert) + pseudo-couches : vias, composants, pastilles, plans.
 */

import * as THREE from 'three';

/** Hauteur y (mm, au-dessus de la face inférieure) de chaque couche cuivre. */
export const LAYER_HEIGHT_MM = Object.freeze([1.5, 0.68, 0.42, 0.12]);
/** Épaisseur du substrat FR4 en mm. */
export const BOARD_THICKNESS_MM = 1.6;
/** y de la face supérieure (base des composants). */
export const BOARD_TOP_MM = BOARD_THICKNESS_MM;

/** Définition des couches cuivre affichées dans le HUD. */
export const COPPER_LAYERS = Object.freeze([
  { index: 0, key: 'F.Cu', label: 'F.Cu — pistes supérieures', color: 0x56d4dd, css: '#56D4DD' },
  { index: 1, key: 'GND', label: 'GND — plan de masse', color: 0xbc8cff, css: '#BC8CFF' },
  { index: 2, key: 'PWR', label: 'PWR — plan d’alimentation', color: 0xf0883e, css: '#F0883E' },
  { index: 3, key: 'B.Cu', label: 'B.Cu — pistes inférieures', color: 0x3fb950, css: '#3FB950' },
]);

/** Couleur par bloc fonctionnel (peuplé par le planner multi-agents). */
export const BLOCK_COLORS = Object.freeze({
  mcu: 0x56d4dd,
  rf: 0xbc8cff,
  power: 0xf0883e,
  io: 0x3fb950,
  memory: 0xd2a8ff,
  sensor: 0x79c0ff,
  audio: 0xff7b72,
  clock: 0xe3b341,
  default: 0x8b949e,
});

export function colorForBlock(block) {
  return BLOCK_COLORS[block] || BLOCK_COLORS.default;
}

/** Couleur three.js d'une piste selon la couche cuivre. */
export function colorForLayer(layerIndex) {
  const def = COPPER_LAYERS.find((l) => l.index === Number(layerIndex));
  return def ? def.color : 0x8b949e;
}

/** Convertit la couche d'un événement (payload) en clé de groupe. */
export function eventLayerToKey(layer) {
  if (layer === 'top' || layer === 'F.Cu') return '0';
  if (layer === 'bottom' || layer === 'B.Cu') return '3';
  const n = Number(layer);
  if (Number.isFinite(n)) return String(Math.min(3, Math.max(0, Math.round(n))));
  return '0';
}

export class LayerManager {
  /** @param {THREE.Scene} scene */
  constructor(scene) {
    this.root = new THREE.Group();
    this.root.name = 'layers';
    scene.add(this.root);
    this._groups = new Map();
    this._visible = new Map();
    // Groupes : 4 couches cuivre + pseudo-couches outils.
    for (const def of COPPER_LAYERS) this._makeGroup(def.key);
    for (const key of ['vias', 'components', 'pads', 'board', 'zones']) this._makeGroup(key);
  }

  _makeGroup(key) {
    const group = new THREE.Group();
    group.name = `layer:${key}`;
    this.root.add(group);
    this._groups.set(key, group);
    this._visible.set(key, true);
    return group;
  }

  /** Groupe three.js d'une couche (créé à la volée si inconnu). */
  group(key) {
    let g = this._groups.get(key);
    if (!g) g = this._makeGroup(key);
    return g;
  }

  /** Rattache un objet à une couche. */
  addTo(key, object3d) {
    this.group(key).add(object3d);
  }

  isVisible(key) {
    return this._visible.get(key) !== false;
  }

  setVisible(key, visible) {
    this._visible.set(key, !!visible);
    const g = this._groups.get(key);
    if (g) g.visible = !!visible;
  }

  /** Applique l'opacité à tous les matériaux de la couche. */
  setOpacity(key, opacity) {
    const g = this._groups.get(key);
    if (!g) return;
    const value = Math.min(1, Math.max(0, Number(opacity) || 0));
    g.traverse((obj) => {
      if (obj.material) {
        const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
        for (const m of mats) {
          if ('opacity' in m && m.transparent) m.opacity = value;
        }
      }
    });
  }

  listKeys() {
    return [...this._groups.keys()];
  }

  dispose() {
    this.root.removeFromParent();
    this._groups.clear();
    this._visible.clear();
  }
}
