/* Contrôles caméra maison (orbite / zoom / pan) — pointer events, zéro
 * dépendance externe (pas d'OrbitControls des examples three).
 *
 * - clic gauche : orbite ; clic droit / shift+milieu : pan ; molette : zoom.
 * - tactile : 1 doigt orbite, 2 doigts pinch zoom + pan.
 * - lissage par amortissement exponentiel, limites strictes (au-dessus de la
 *   carte, distance bornée, cible confinée au plateau).
 * - presets de vue : top (dessus), iso (isométrique), side (de face).
 */

import * as THREE from 'three';

const DEFAULTS = {
  minRadius: 15,
  maxRadius: 1200,
  minPhi: 0.05, // quasi zénith — jamais sous l'horizon
  maxPhi: Math.PI / 2 - 0.03,
  damping: 9, // amortissement par seconde (lerp exponentiel)
  rotateSpeed: 0.0052,
  zoomSpeed: 1.0,
  bounds: { width: 260, height: 260 }, // confinage du point-cible (mm)
};

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export class OrbitPanControls {
  /**
   * @param {THREE.PerspectiveCamera} camera
   * @param {HTMLElement} domElement canvas (ou son parent) captant les events
   * @param {object} [options]
   */
  constructor(camera, domElement, options = {}) {
    this.camera = camera;
    this.domElement = domElement;
    this.opts = { ...DEFAULTS, ...options };

    this.target = new THREE.Vector3(0, 1, 0);
    this._targetGoal = this.target.clone();
    this._sph = { radius: options.radius || 170, theta: Math.PI / 4, phi: 0.95 };
    this._sphGoal = { ...this._sph };

    /** @type {Map<number, {x:number, y:number}>} */
    this._pointers = new Map();
    this._mode = null; // 'orbit' | 'pan' | 'pinch' | null
    this._pinchDist = 0;

    this._onPointerDown = this._onPointerDown.bind(this);
    this._onPointerMove = this._onPointerMove.bind(this);
    this._onPointerUp = this._onPointerUp.bind(this);
    this._onWheel = this._onWheel.bind(this);
    this._onContextMenu = (e) => e.preventDefault();
    this._attach();
  }

  _attach() {
    const el = this.domElement;
    el.addEventListener('pointerdown', this._onPointerDown);
    el.addEventListener('pointermove', this._onPointerMove);
    el.addEventListener('pointerup', this._onPointerUp);
    el.addEventListener('pointercancel', this._onPointerUp);
    el.addEventListener('wheel', this._onWheel, { passive: false });
    el.addEventListener('contextmenu', this._onContextMenu);
  }

  dispose() {
    const el = this.domElement;
    el.removeEventListener('pointerdown', this._onPointerDown);
    el.removeEventListener('pointermove', this._onPointerMove);
    el.removeEventListener('pointerup', this._onPointerUp);
    el.removeEventListener('pointercancel', this._onPointerUp);
    el.removeEventListener('wheel', this._onWheel);
    el.removeEventListener('contextmenu', this._onContextMenu);
  }

  /* ---- événements --------------------------------------------------------- */

  _onPointerDown(e) {
    this.domElement.setPointerCapture?.(e.pointerId);
    this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (this._pointers.size === 1) {
      this._mode = e.button === 0 && !e.shiftKey ? 'orbit' : 'pan';
    } else if (this._pointers.size === 2) {
      this._mode = 'pinch';
      this._pinchDist = this._currentPinch();
    }
  }

  _onPointerMove(e) {
    const prev = this._pointers.get(e.pointerId);
    if (!prev) return;
    const dx = e.clientX - prev.x;
    const dy = e.clientY - prev.y;
    this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (this._mode === 'orbit') {
      this._sphGoal.theta -= dx * this.opts.rotateSpeed;
      this._sphGoal.phi = clamp(
        this._sphGoal.phi - dy * this.opts.rotateSpeed,
        this.opts.minPhi,
        this.opts.maxPhi
      );
    } else if (this._mode === 'pan') {
      this._panByPixels(dx, dy);
    } else if (this._mode === 'pinch' && this._pointers.size === 2) {
      const d = this._currentPinch();
      if (this._pinchDist > 0 && d > 0) {
        const factor = this._pinchDist / d; // doigt qui s'écarte → rapproche
        this._sphGoal.radius = clamp(
          this._sphGoal.radius * factor,
          this.opts.minRadius,
          this.opts.maxRadius
        );
      }
      this._pinchDist = d;
      this._panByPixels(dx * 0.5, dy * 0.5);
    }
  }

  _onPointerUp(e) {
    this._pointers.delete(e.pointerId);
    if (this._pointers.size === 0) this._mode = null;
    else if (this._pointers.size === 1) this._mode = 'orbit';
  }

  _onWheel(e) {
    e.preventDefault();
    const factor = Math.exp(e.deltaY * 0.0012 * this.opts.zoomSpeed);
    this._sphGoal.radius = clamp(
      this._sphGoal.radius * factor,
      this.opts.minRadius,
      this.opts.maxRadius
    );
  }

  /* ---- helpers -------------------------------------------------------------- */

  _currentPinch() {
    const pts = [...this._pointers.values()];
    if (pts.length < 2) return 0;
    return Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
  }

  _panByPixels(dx, dy) {
    const factor = this._sphGoal.radius * 0.0011; // échelle perspective
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 0);
    const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 1);
    this._targetGoal.addScaledVector(right, -dx * factor).addScaledVector(up, dy * factor);
    const b = this.opts.bounds;
    this._targetGoal.x = clamp(this._targetGoal.x, -b.width / 2, b.width / 2);
    this._targetGoal.z = clamp(this._targetGoal.z, -b.height / 2, b.height / 2);
    this._targetGoal.y = clamp(this._targetGoal.y, 0, 60);
  }

  /* ---- presets + boucle ------------------------------------------------------ */

  /** Presets de vue : 'top' | 'iso' | 'side'. */
  setPreset(name) {
    if (name === 'top') {
      this._sphGoal.phi = this.opts.minPhi;
      this._sphGoal.theta = 0;
    } else if (name === 'iso') {
      this._sphGoal.phi = 0.9;
      this._sphGoal.theta = Math.PI / 4;
    } else if (name === 'side') {
      this._sphGoal.phi = this.opts.maxPhi;
      this._sphGoal.theta = 0;
    }
  }

  /** Cadre la caméra sur une carte de dimensions (widthMm × heightMm). */
  frameBoard(widthMm, heightMm) {
    this._targetGoal.set(0, 1, 0);
    this._sphGoal.radius = clamp(
      Math.max(widthMm, heightMm) * 1.45,
      this.opts.minRadius,
      this.opts.maxRadius
    );
  }

  /** Intégration explicite chaque frame (dt en secondes). */
  update(dt) {
    const k = 1 - Math.exp(-this.opts.damping * Math.min(dt, 0.1));
    this._sph.radius += (this._sphGoal.radius - this._sph.radius) * k;
    this._sph.theta += (this._sphGoal.theta - this._sph.theta) * k;
    this._sph.phi += (this._sphGoal.phi - this._sph.phi) * k;
    this.target.lerp(this._targetGoal, k);

    const { radius, theta, phi } = this._sph;
    this.camera.position.set(
      this.target.x + radius * Math.sin(phi) * Math.sin(theta),
      this.target.y + radius * Math.cos(phi),
      this.target.z + radius * Math.sin(phi) * Math.cos(theta)
    );
    this.camera.lookAt(this.target);
  }
}
