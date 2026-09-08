/* Moteur de rendu WebGL du viewer 3D — Three.js pur, 1 unité = 1 mm.
 *
 * - Scène, lumières, grille, substrat FR4 semi-transparent (couches internes
 *   visibles par transparence, à la manière d'une vue KiCad en cutaway).
 * - Composants : InstancedMesh par bloc fonctionnel (geometry boîte unitaire,
 *   scale par instance) → 60 fps même sur des designs denses.
 * - Pastilles : InstancedMesh cylindres dorés (plafonnées à 12 000).
 * - Vias : InstancedMesh cylindres par net.
 * - Pistes : rubans plats (2 triangles par segment) avec couleur par couche,
 *   tracés progressivement via geometry.setDrawRange à l'arrivée des events
 *   net_routed (mises à jour incrémentales : seul le net modifié est reconstruit).
 * - Sélection : raycaster sur les InstancedMesh → { ref, bbox } board (mm).
 * - Tweens : component_moved → interpolation de la matrice d'instance.
 * - session_resumed → reconstruction complète depuis l'état resynchronisé.
 *
 * Aucun appel réseau ici : les données arrivent via buildBoardScene/applyEvent.
 */

import * as THREE from 'three';
import { OrbitPanControls } from './cameraControls.js';
import {
  LayerManager,
  LAYER_HEIGHT_MM,
  BOARD_TOP_MM,
  colorForBlock,
  colorForLayer,
  eventLayerToKey,
} from './layerManager.js';

const DEG2RAD = Math.PI / 180;
const MAX_PADS = 12000;
const SELECT_COLOR = 0xffffff;

const lerp = (a, b, t) => a + (b - a) * t;
const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function hashString(str) {
  let h = 0;
  for (let i = 0; i < str.length; i += 1) h = (h * 31 + str.charCodeAt(i)) | 0;
  return Math.abs(h);
}

export class ViewerRenderer {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {object} [callbacks] onStats({fps}) · onPick(ref, bbox) · onDrc(payload) · onResync()
   */
  constructor(canvas, callbacks = {}) {
    if (!canvas) throw new Error('[ViewerRenderer] canvas requis');
    this.canvas = canvas;
    this.callbacks = callbacks;
    this.boardMeta = { widthMm: 0, heightMm: 0, netsTotal: 0, netsRouted: 0, viaCount: 0 };

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0d1117);

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 5000);
    this.camera.position.set(130, 130, 130);

    this._renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this._renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    this._buildEnvironment();
    this.controls = new OrbitPanControls(this.camera, canvas, { radius: 170 });
    this.layers = new LayerManager(this.scene);

    /* état design */
    this._boardGroup = null;
    /** @type {Map<string, object>} ref → enregistrement d'instance */
    this._compRecords = new Map();
    /** @type {Map<string, THREE.InstancedMesh>} bloc fonctionnel → mesh */
    this._blockMeshes = new Map();
    /** @type {Map<string, {parts: Array, viaMesh: object|null}>} net → rendu */
    this._netMeshes = new Map();
    this._padMesh = null;
    /** @type {Array<object>} tweens en cours */
    this._tweens = [];
    this._selectedRef = null;

    /* sélection : boîte filaire blanche */
    const selEdges = new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1));
    this._selectionBox = new THREE.LineSegments(
      selEdges,
      new THREE.LineBasicMaterial({ color: SELECT_COLOR, transparent: true, opacity: 0.95 })
    );
    this._selectionBox.visible = false;
    this.scene.add(this._selectionBox);

    this._raycaster = new THREE.Raycaster();

    /* resize réactif au conteneur */
    this._ro = new ResizeObserver(() => this._resize());
    if (canvas.parentElement) this._ro.observe(canvas.parentElement);
    this._resize();

    /* boucle de rendu */
    this._disposed = false;
    this._raf = null;
    this._lastT = performance.now();
    this._fpsFrames = 0;
    this._fpsElapsed = 0;
    this._lastFps = 0;
    this._startLoop();
  }

  /* ------------------------------------------------------------------ scène */

  _buildEnvironment() {
    this.scene.add(new THREE.HemisphereLight(0x8fb5c9, 0x0a0e13, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 1.15);
    key.position.set(80, 140, 60);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0x56d4dd, 0.3);
    fill.position.set(-70, 60, -90);
    this.scene.add(fill);

    const grid = new THREE.GridHelper(500, 50, 0x1d2733, 0x141a22);
    grid.position.y = -0.6;
    grid.name = 'ground-grid';
    this.scene.add(grid);
  }

  /** Vide toute la scène carte (dispose complet des ressources GPU). */
  clearBoard() {
    this._tweens = [];
    if (this._boardGroup) {
      this._boardGroup.traverse((obj) => {
        if (obj.geometry) obj.geometry.dispose();
        if (obj.material) {
          const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
          for (const m of mats) m.dispose();
        }
      });
      this._boardGroup.removeFromParent();
      this._boardGroup = null;
    }
    this._compRecords.clear();
    this._blockMeshes.clear();
    this._netMeshes.clear();
    this._padMesh = null;
    this._selectedRef = null;
    this._selectionBox.visible = false;
    this.boardMeta = { widthMm: 0, heightMm: 0, netsTotal: 0, netsRouted: 0, viaCount: 0 };
  }

  /**
   * Construit (ou reconstruit) la scène depuis l'état du design.
   * Accepte l'état complet { board, version, … } ou le board brut.
   */
  buildBoardScene(data) {
    const board = data && data.board ? data.board : data;
    if (!board) return;
    this.clearBoard();

    const w = Number(board.width_mm) || 100;
    const h = Number(board.height_mm) || 80;
    const group = new THREE.Group();
    group.name = 'board';
    this._boardGroup = group;
    this.boardMeta = { widthMm: w, heightMm: h, netsTotal: 0, netsRouted: 0, viaCount: 0 };

    /* substrat FR4 semi-transparent (cutaway : couches internes visibles) */
    const substrate = new THREE.Mesh(
      new THREE.BoxGeometry(w, 1.6, h),
      new THREE.MeshStandardMaterial({
        color: 0x11402f,
        roughness: 0.9,
        metalness: 0.05,
        transparent: true,
        opacity: 0.55,
      })
    );
    substrate.position.y = 0.8;
    substrate.renderOrder = 0;
    this.layers.addTo('board', substrate);

    /* contour soie */
    const outline = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(w, 1.6, h)),
      new THREE.LineBasicMaterial({ color: 0xd7e3dd, transparent: true, opacity: 0.35 })
    );
    outline.position.y = 0.8;
    this.layers.addTo('board', outline);

    /* zones : keepout / thermique / fonctionnelle */
    for (const zone of board.zones || []) {
      const zw = Math.abs(Number(zone.x_max_mm) - Number(zone.x_min_mm));
      const zh = Math.abs(Number(zone.y_max_mm) - Number(zone.y_min_mm));
      if (!zw || !zh) continue;
      const color =
        zone.kind === 'thermal' ? 0xf0883e : zone.kind === 'functional' ? 0x56d4dd : 0xf85149;
      const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(zw, zh),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.1, depthWrite: false })
      );
      plane.rotation.x = -Math.PI / 2;
      plane.position.set(
        (Number(zone.x_min_mm) + Number(zone.x_max_mm)) / 2,
        BOARD_TOP_MM + 0.02,
        -(Number(zone.y_min_mm) + Number(zone.y_max_mm)) / 2
      );
      plane.renderOrder = 1;
      this.layers.addTo('zones', plane);
    }

    /* plans de masse / alimentation */
    const planeDefs = [
      { index: 1, opacity: 0.16 },
      { index: 2, opacity: 0.1 },
    ];
    for (const def of planeDefs) {
      const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(Math.max(1, w - 3), Math.max(1, h - 3)),
        new THREE.MeshBasicMaterial({
          color: colorForLayer(def.index),
          transparent: true,
          opacity: def.opacity,
          side: THREE.DoubleSide,
          depthWrite: false,
        })
      );
      plane.rotation.x = -Math.PI / 2;
      plane.position.y = LAYER_HEIGHT_MM[def.index];
      plane.renderOrder = 1;
      this.layers.addTo(String(def.index), plane);
    }

    /* composants — un InstancedMesh par bloc fonctionnel */
    const comps = board.components || {};
    const placements = board.placements || {};
    const byBlock = new Map();
    for (const [ref, comp] of Object.entries(comps)) {
      const block = comp.functional_block || 'default';
      if (!byBlock.has(block)) byBlock.set(block, []);
      byBlock.get(block).push({
        ref,
        comp,
        placement: placements[ref] || { x_mm: 0, y_mm: 0, rotation_deg: 0, layer: 0 },
      });
    }

    const baseColor = new THREE.Color();
    const tmpColor = new THREE.Color();
    for (const [block, items] of byBlock.entries()) {
      const unitBox = new THREE.BoxGeometry(1, 1, 1);
      const material = new THREE.MeshStandardMaterial({ roughness: 0.45, metalness: 0.35 });
      const mesh = new THREE.InstancedMesh(unitBox, material, items.length);
      mesh.userData.block = block;
      mesh.userData.refs = items.map((it) => it.ref);
      baseColor.setHex(colorForBlock(block));

      items.forEach((item, i) => {
        const cw = Number(item.comp.width_mm) || 4;
        const chh = Number(item.comp.height_mm) || 4;
        const bodyH = (item.comp.pins || 0) > 48 ? 3.2 : (item.comp.pins || 0) > 16 ? 2.4 : 1.8;
        const cx = Number(item.placement.x_mm) || 0;
        const cy = Number(item.placement.y_mm) || 0;
        const rot = Number(item.placement.rotation_deg) || 0;
        this._compRecords.set(item.ref, {
          ref: item.ref,
          mesh,
          index: i,
          w: cw,
          h: chh,
          bodyH,
          cx,
          cy,
          rot,
          block,
          previewFrom: null,
        });
        this._setInstanceTransform(this._compRecords.get(item.ref), cx, cy, rot);
        tmpColor.copy(baseColor).offsetHSL(0, 0, ((hashString(item.ref) % 9) - 4) / 120);
        mesh.setColorAt(i, tmpColor);
      });
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      this._blockMeshes.set(block, mesh);
      this.layers.addTo('components', mesh);
    }

    /* pastilles (offsets relatifs au centre, tournés avec le composant) */
    const padGeo = new THREE.CylinderGeometry(0.3, 0.3, 0.06, 12);
    const padMat = new THREE.MeshStandardMaterial({
      color: 0xe3b341,
      roughness: 0.3,
      metalness: 0.8,
    });
    const padList = [];
    outer: for (const [ref, comp] of Object.entries(comps)) {
      const rec = this._compRecords.get(ref);
      const rot = rec ? rec.rot * DEG2RAD : 0;
      const cos = Math.cos(rot);
      const sin = Math.sin(rot);
      for (const pad of comp.pads || []) {
        if (padList.length >= MAX_PADS) break outer;
        const ox = Number(pad.x_mm) || 0;
        const oy = Number(pad.y_mm) || 0;
        padList.push({
          x: (rec ? rec.cx : 0) + ox * cos - oy * sin,
          y: (rec ? rec.cy : 0) + ox * sin + oy * cos,
          diameter: Number(pad.diameter_mm) || 0.6,
        });
      }
    }
    if (padList.length) {
      const padMesh = new THREE.InstancedMesh(padGeo, padMat, padList.length);
      const m = new THREE.Matrix4();
      const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), 0);
      padList.forEach((pad, i) => {
        const s = pad.diameter / 0.6;
        m.compose(
          new THREE.Vector3(pad.x, BOARD_TOP_MM + 0.03, -pad.y),
          q,
          new THREE.Vector3(s, 1, s)
        );
        padMesh.setMatrixAt(i, m);
      });
      padMesh.instanceMatrix.needsUpdate = true;
      this._padMesh = padMesh;
      this.layers.addTo('pads', padMesh);
    }

    /* nets déjà routés dans l'état (sans animation) */
    const nets = board.nets || {};
    this.boardMeta.netsTotal = Object.keys(nets).length;
    for (const [name, net] of Object.entries(nets)) {
      const segs = (net && net.routed_segments) || [];
      if (segs.length) {
        this.boardMeta.netsRouted += 1;
        this._addNetGeometry(name, segs, { animate: false });
      }
    }
    this.boardMeta.viaCount = this._countVias(nets);

    this.scene.add(group);
    this.controls.frameBoard(w, h);
  }

  _countVias(nets) {
    let count = 0;
    for (const net of Object.values(nets)) {
      for (const seg of (net && net.routed_segments) || []) {
        if (seg.is_via) count += 1;
      }
    }
    return count;
  }

  /* -------------------------------------------------------------- pistes */

  /**
   * (Re)construit le rendu d'un net — ruban plat par couche + vias.
   * animate=true → tracé progressif via setDrawRange (event net_routed).
   */
  _addNetGeometry(netName, segments, { animate = false } = {}) {
    const old = this._netMeshes.get(netName);
    if (old) {
      for (const part of old.parts) {
        part.mesh.removeFromParent();
        part.mesh.geometry.dispose();
        part.mesh.material.dispose();
      }
      if (old.viaMesh) {
        old.viaMesh.mesh.removeFromParent();
        old.viaMesh.mesh.geometry.dispose();
        old.viaMesh.mesh.material.dispose();
      }
    }

    const byLayer = new Map();
    const viaPoints = [];
    for (const seg of segments || []) {
      if (seg.is_via || (seg.x1_mm === seg.x2_mm && seg.y1_mm === seg.y2_mm)) {
        viaPoints.push(seg);
      } else {
        const key = eventLayerToKey(seg.layer);
        if (!byLayer.has(key)) byLayer.set(key, []);
        byLayer.get(key).push(seg);
      }
    }

    const parts = [];
    const color = new THREE.Color();
    for (const [layerKey, segs] of byLayer.entries()) {
      const positions = [];
      const colors = [];
      const indices = [];
      let base = 0;
      const y = LAYER_HEIGHT_MM[Number(layerKey)] + 0.03;
      color.setHex(colorForLayer(Number(layerKey)));
      for (const seg of segs) {
        const halfW = Math.max(0.09, (Number(seg.width_mm) || 0.2) / 2);
        const dx = seg.x2_mm - seg.x1_mm;
        const dz = -(seg.y2_mm - seg.y1_mm); // board y → -z
        const len = Math.hypot(dx, dz) || 1e-6;
        const nx = (-dz / len) * halfW;
        const nz = (dx / len) * halfW;
        const p1x = seg.x1_mm;
        const p1z = -seg.y1_mm;
        const p2x = seg.x2_mm;
        const p2z = -seg.y2_mm;
        // quadrilatère : a/b sur p1, c/d sur p2
        positions.push(
          p1x + nx, y, p1z + nz,
          p1x - nx, y, p1z - nz,
          p2x + nx, y, p2z + nz,
          p2x - nx, y, p2z - nz
        );
        for (let k = 0; k < 4; k += 1) colors.push(color.r, color.g, color.b);
        indices.push(base, base + 1, base + 3, base, base + 3, base + 2);
        base += 4;
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
      geo.setIndex(indices);
      geo.computeBoundingSphere();
      const mat = new THREE.MeshBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.92,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.renderOrder = 2;
      mesh.frustumCulled = false; // drawRange animé : évite les disparitions
      this.layers.addTo(layerKey, mesh);
      parts.push({ mesh, count: indices.length });
      geo.setDrawRange(0, animate ? 0 : indices.length);
    }

    /* vias du net */
    let viaMesh = null;
    if (viaPoints.length) {
      const viaGeo = new THREE.CylinderGeometry(0.32, 0.32, 1.9, 10);
      const viaMat = new THREE.MeshStandardMaterial({
        color: 0xe3b341,
        roughness: 0.35,
        metalness: 0.7,
      });
      const im = new THREE.InstancedMesh(viaGeo, viaMat, viaPoints.length);
      const m = new THREE.Matrix4();
      const q = new THREE.Quaternion();
      viaPoints.forEach((seg, i) => {
        m.compose(
          new THREE.Vector3(seg.x1_mm ?? seg.x2_mm ?? 0, 0.8, -(seg.y1_mm ?? seg.y2_mm ?? 0)),
          q,
          new THREE.Vector3(1, 1, 1)
        );
        im.setMatrixAt(i, m);
      });
      im.instanceMatrix.needsUpdate = true;
      this.layers.addTo('vias', im);
      viaMesh = { mesh: im };
    }

    this._netMeshes.set(netName, { parts, viaMesh });
    if (animate && parts.length) {
      this._tweens.push({ kind: 'net', parts, start: performance.now(), dur: 550 });
    }
    return { parts, viaMesh };
  }

  /* ---------------------------------------------------------- événements */

  /**
   * Applique un événement contractuel — mises à jour incrémentales uniquement :
   * component_moved (tween), net_routed (tracé progressif), drc_update (HUD),
   * session_resumed (reconstruction depuis l'état frais).
   */
  applyEvent(event) {
    if (!event || !event.type) return;
    const p = event.payload || {};
    if (event.type === 'component_moved') {
      const rec = this._compRecords.get(p.ref || p.component || '');
      if (!rec) return;
      const to = {
        x: Number(p.x_mm ?? rec.cx) || 0,
        y: Number(p.y_mm ?? rec.cy) || 0,
        rot: Number(p.rotation_deg ?? rec.rot) || 0,
      };
      this._tweens.push({
        kind: 'comp',
        rec,
        from: { x: rec.cx, y: rec.cy, rot: rec.rot },
        to,
        start: performance.now(),
        dur: 300,
      });
    } else if (event.type === 'net_routed') {
      const netName = p.net || p.net_name || '';
      const segs = Array.isArray(p.segments) ? p.segments : [];
      if (!netName || !segs.length) return;
      this._addNetGeometry(netName, segs, { animate: true });
      this.boardMeta.netsRouted = Math.max(
        this.boardMeta.netsRouted + (this._netMeshes.has(netName) ? 0 : 1),
        1
      );
    } else if (event.type === 'drc_update') {
      this._safeCb('onDrc', p);
    } else if (event.type === 'session_resumed' && p.state) {
      this.buildBoardScene(p.state);
      this._safeCb('onResync', p);
    }
  }

  /* ------------------------------------------------------- sélection raycast */

  /** Raycast écran → composant. Retourne { ref, block, bbox } ou null. */
  pickAt(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const nx = ((clientX - rect.left) / rect.width) * 2 - 1;
    const ny = -((clientY - rect.top) / rect.height) * 2 + 1;
    this._raycaster.setFromCamera(new THREE.Vector2(nx, ny), this.camera);
    const hits = this._raycaster.intersectObjects([...this._blockMeshes.values()], false);
    if (!hits.length) {
      this.selectRef(null);
      return null;
    }
    const hit = hits[0];
    const ref = hit.object.userData && hit.object.userData.refs
      ? hit.object.userData.refs[hit.instanceId]
      : null;
    const rec = ref ? this._compRecords.get(ref) : null;
    if (!rec) return null;
    this.selectRef(ref);
    return { ref, block: rec.block, bbox: this.bboxOf(ref) };
  }

  /** Sélection programmatique d'un composant (mise en surbrillance). */
  selectRef(ref) {
    this._selectedRef = ref || null;
    const rec = ref ? this._compRecords.get(ref) : null;
    if (!rec) {
      this._selectionBox.visible = false;
      return;
    }
    this._placeSelectionBox(rec);
    this._selectionBox.visible = true;
  }

  _placeSelectionBox(rec) {
    const margin = 1.15;
    this._selectionBox.position.set(rec.cx, BOARD_TOP_MM + rec.bodyH / 2, -rec.cy);
    this._selectionBox.scale.set(rec.w * margin, rec.bodyH * 1.3, rec.h * margin);
    this._selectionBox.rotation.y = rec.rot * DEG2RAD;
  }

  /** Bounding box board (mm, axes carte) d'un composant — miroir de design_model. */
  bboxOf(ref) {
    const rec = this._compRecords.get(ref);
    if (!rec) return null;
    let hw = rec.w / 2;
    let hh = rec.h / 2;
    if (Math.abs(Math.sin(rec.rot * DEG2RAD)) > 0.5) {
      const t = hw;
      hw = hh;
      hh = t;
    }
    return { xMin: rec.cx - hw, yMin: rec.cy - hh, xMax: rec.cx + hw, yMax: rec.cy + hh };
  }

  /* ------------------------------------------------ aperçu avant commit */

  /** Applique localement une transformation (revue accept/reject, sans event). */
  previewTransform(ref, transform) {
    const rec = this._compRecords.get(ref);
    if (!rec || !transform) return false;
    if (!rec.previewFrom) rec.previewFrom = { x: rec.cx, y: rec.cy, rot: rec.rot };
    const dx = Number(transform.dx_mm) || 0;
    const dy = Number(transform.dy_mm) || 0;
    const rot = Number(transform.rot_deg) || 0;
    this._setInstanceTransform(rec, rec.cx + dx, rec.cy + dy, rec.rot + rot);
    return true;
  }

  /** Annule l'aperçu — restaure la pose d'origine. */
  cancelPreview(ref) {
    const rec = this._compRecords.get(ref);
    if (!rec || !rec.previewFrom) return;
    const { x, y, rot } = rec.previewFrom;
    rec.previewFrom = null;
    this._setInstanceTransform(rec, x, y, rot);
  }

  /** Valide l'aperçu — la pose courante devient la pose de référence. */
  commitPreview(ref) {
    const rec = this._compRecords.get(ref);
    if (rec) rec.previewFrom = null;
  }

  _setInstanceTransform(rec, xMm, yMm, rotDeg) {
    const quat = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), rotDeg * DEG2RAD);
    const matrix = new THREE.Matrix4().compose(
      new THREE.Vector3(xMm, BOARD_TOP_MM + rec.bodyH / 2, -yMm),
      quat,
      new THREE.Vector3(rec.w, rec.bodyH, rec.h)
    );
    rec.mesh.setMatrixAt(rec.index, matrix);
    rec.mesh.instanceMatrix.needsUpdate = true;
    rec.cx = xMm;
    rec.cy = yMm;
    rec.rot = rotDeg;
    if (this._selectedRef === rec.ref) this._placeSelectionBox(rec);
  }

  /* ---------------------------------------------------- projection 3D → 2D */

  /**
   * Projette une bbox board (mm) en rectangle écran (px, relatif au canvas).
   * Utilisé par l'overlay d'impact de l'éditeur chirurgical.
   */
  projectBBox(bbox, { marginMm = 2, heightMm = 5 } = {}) {
    const rect = this.canvas.getBoundingClientRect();
    const empty = { visible: false, left: 0, top: 0, width: 0, height: 0 };
    if (!bbox || !rect.width || !rect.height) return empty;
    const xMin = bbox.xMin - marginMm;
    const yMin = bbox.yMin - marginMm;
    const xMax = bbox.xMax + marginMm;
    const yMax = bbox.yMax + marginMm;
    const corners = [
      [xMin, yMin],
      [xMax, yMin],
      [xMax, yMax],
      [xMin, yMax],
    ];
    const view = new THREE.Vector3();
    const ndc = new THREE.Vector3();
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let anyVisible = false;
    for (const [bx, by] of corners) {
      for (const yy of [0.5, BOARD_TOP_MM + heightMm]) {
        view.set(bx, yy, -by).applyMatrix4(this.camera.matrixWorldInverse);
        if (view.z > 0) continue; // derrière la caméra
        anyVisible = true;
        ndc.set(bx, yy, -by).project(this.camera);
        const px = (ndc.x * 0.5 + 0.5) * rect.width;
        const py = (-ndc.y * 0.5 + 0.5) * rect.height;
        minX = Math.min(minX, px);
        minY = Math.min(minY, py);
        maxX = Math.max(maxX, px);
        maxY = Math.max(maxY, py);
      }
    }
    if (!anyVisible) return empty;
    return {
      visible: true,
      left: minX,
      top: minY,
      width: Math.max(2, maxX - minX),
      height: Math.max(2, maxY - minY),
    };
  }

  /* ------------------------------------------------------------- boucle */

  _startLoop() {
    const tick = (t) => {
      if (this._disposed) return;
      this._raf = requestAnimationFrame(tick);
      const dt = Math.min(0.05, Math.max(0.001, (t - this._lastT) / 1000));
      this._lastT = t;
      if (document.hidden) return; // throttle : zéro rendu en arrière-plan
      this.controls.update(dt);
      this._updateTweens(t);
      this._fpsFrames += 1;
      this._fpsElapsed += dt;
      if (this._fpsElapsed >= 0.5) {
        const fps = Math.round(this._fpsFrames / this._fpsElapsed);
        this._fpsFrames = 0;
        this._fpsElapsed = 0;
        if (fps !== this._lastFps) {
          this._lastFps = fps;
          this._safeCb('onStats', { fps });
        }
      }
      this._renderer.render(this.scene, this.camera);
    };
    this._raf = requestAnimationFrame(tick);
  }

  _updateTweens(now) {
    for (let i = this._tweens.length - 1; i >= 0; i -= 1) {
      const tw = this._tweens[i];
      const t = Math.min(1, (now - tw.start) / tw.dur);
      const k = easeInOutCubic(t);
      if (tw.kind === 'comp') {
        this._setInstanceTransform(
          tw.rec,
          lerp(tw.from.x, tw.to.x, k),
          lerp(tw.from.y, tw.to.y, k),
          lerp(tw.from.rot, tw.to.rot, k)
        );
      } else if (tw.kind === 'net') {
        for (const part of tw.parts) {
          part.mesh.geometry.setDrawRange(0, Math.floor(k * part.count));
        }
      }
      if (t >= 1) {
        if (tw.kind === 'net') {
          for (const part of tw.parts) part.mesh.geometry.setDrawRange(0, part.count);
        }
        this._tweens.splice(i, 1);
      }
    }
  }

  _resize() {
    const parent = this.canvas.parentElement;
    const w = parent ? parent.clientWidth : this.canvas.clientWidth;
    const h = parent ? parent.clientHeight : this.canvas.clientHeight;
    if (!w || !h) return;
    this._renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _safeCb(name, arg) {
    try {
      if (typeof this.callbacks[name] === 'function') this.callbacks[name](arg);
    } catch (err) {
      console.error(`[ViewerRenderer] callback ${name}`, err);
    }
  }

  /** Libération complète : boucle rAF, observers, contrôles, GPU. */
  dispose() {
    this._disposed = true;
    if (this._raf) cancelAnimationFrame(this._raf);
    if (this._ro) this._ro.disconnect();
    this.controls.dispose();
    this.clearBoard();
    this._selectionBox.geometry.dispose();
    this._selectionBox.material.dispose();
    this._selectionBox.removeFromParent();
    this.layers.dispose();
    this._renderer.dispose();
    if (typeof this._renderer.forceContextLoss === 'function') this._renderer.forceContextLoss();
    this.scene.clear();
  }
}
