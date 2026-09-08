/* Utilitaires de bounding box (espace board, mm) + projection 3D → 2D.
 *
 * Une bbox est un objet { xMin, yMin, xMax, yMax } en millimètres, axes carte
 * (x → droite, y → haut) — même convention que common/design_model.
 */

import * as THREE from 'three';

/** Crée une bbox à partir de deux coins. */
export function makeBBox(xMin, yMin, xMax, yMax) {
  return {
    xMin: Math.min(xMin, xMax),
    yMin: Math.min(yMin, yMax),
    xMax: Math.max(xMin, xMax),
    yMax: Math.max(yMin, yMax),
  };
}

/** Bbox nulle (inverse) — neutre pour union(). */
export function emptyBBox() {
  return { xMin: Infinity, yMin: Infinity, xMax: -Infinity, yMax: -Infinity };
}

/** Agrandit une bbox de `margin` mm dans les 4 directions (immutabilité). */
export function expand(bbox, margin) {
  if (!bbox) return null;
  return {
    xMin: bbox.xMin - margin,
    yMin: bbox.yMin - margin,
    xMax: bbox.xMax + margin,
    yMax: bbox.yMax + margin,
  };
}

/** Union de deux bbox (null-safe). */
export function union(a, b) {
  if (!a) return b || null;
  if (!b) return a;
  return {
    xMin: Math.min(a.xMin, b.xMin),
    yMin: Math.min(a.yMin, b.yMin),
    xMax: Math.max(a.xMax, b.xMax),
    yMax: Math.max(a.yMax, b.yMax),
  };
}

/** Bbox d'un composant placé (taille + pose), marge optionnelle. */
export function bboxFromPlacement(widthMm, heightMm, xMm, yMm, rotationDeg, marginMm = 0) {
  let hw = widthMm / 2;
  let hh = heightMm / 2;
  if (Math.abs(Math.sin((Number(rotationDeg) || 0) * (Math.PI / 180))) > 0.5) {
    const t = hw;
    hw = hh;
    hh = t; // rotation ±90° : empreinte pivotée
  }
  return expand(
    {
      xMin: xMm - hw,
      yMin: yMm - hh,
      xMax: xMm + hw,
      yMax: yMm + hh,
    },
    marginMm
  );
}

/** true si le point (x, y) est dans la bbox (marge incluse). */
export function bboxContains(bbox, x, y) {
  if (!bbox) return false;
  return x >= bbox.xMin && x <= bbox.xMax && y >= bbox.yMin && y <= bbox.yMax;
}

/** true si deux bbox se recouvrent. */
export function bboxIntersects(a, b) {
  if (!a || !b) return false;
  return !(a.xMax < b.xMin || b.xMax < a.xMin || a.yMax < b.yMin || b.yMax < a.yMin);
}

/** Dimensions (largeur × hauteur en mm). */
export function bboxDims(bbox) {
  if (!bbox) return { w: 0, h: 0 };
  return { w: bbox.xMax - bbox.xMin, h: bbox.yMax - bbox.yMin };
}

/** Sérialisation lisible pour l'UI : « x: 10.0 → 24.5 · y: 8.2 → 20.0 ». */
export function serializeBBox(bbox) {
  if (!bbox) return '—';
  const fmt = (v) => Number(v).toFixed(1);
  return `x: ${fmt(bbox.xMin)} → ${fmt(bbox.xMax)} · y: ${fmt(bbox.yMin)} → ${fmt(bbox.yMax)}`;
}

/**
 * Projette une bbox board sur l'écran : rectangle {left, top, width, height}
 * en pixels relatifs au canvas. Utilise la caméra du viewer (three.js).
 */
export function projectBBoxToScreen(bbox, camera, canvasEl, { marginMm = 2, heightMm = 5 } = {}) {
  const empty = { visible: false, left: 0, top: 0, width: 0, height: 0 };
  if (!bbox || !camera || !canvasEl) return empty;
  const rect = canvasEl.getBoundingClientRect();
  if (!rect.width || !rect.height) return empty;

  const expanded = expand(bbox, marginMm);
  const corners = [
    [expanded.xMin, expanded.yMin],
    [expanded.xMax, expanded.yMin],
    [expanded.xMax, expanded.yMax],
    [expanded.xMin, expanded.yMax],
  ];
  const view = new THREE.Vector3();
  const ndc = new THREE.Vector3();
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let anyVisible = false;
  for (const [bx, by] of corners) {
    for (const yy of [0.5, heightMm]) {
      view.set(bx, yy, -by).applyMatrix4(camera.matrixWorldInverse);
      if (view.z > 0) continue; // derrière la caméra
      anyVisible = true;
      ndc.set(bx, yy, -by).project(camera);
      minX = Math.min(minX, (ndc.x * 0.5 + 0.5) * rect.width);
      maxX = Math.max(maxX, (ndc.x * 0.5 + 0.5) * rect.width);
      minY = Math.min(minY, (-ndc.y * 0.5 + 0.5) * rect.height);
      maxY = Math.max(maxY, (-ndc.y * 0.5 + 0.5) * rect.height);
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

/** Positionne un overlay DOM sur le canvas à partir du rectangle projeté. */
export function positionOverlay(el, screenRect) {
  if (!el || !screenRect) return;
  if (!screenRect.visible) {
    el.style.display = 'none';
    return;
  }
  el.style.display = 'block';
  el.style.left = `${screenRect.left}px`;
  el.style.top = `${screenRect.top}px`;
  el.style.width = `${screenRect.width}px`;
  el.style.height = `${screenRect.height}px`;
}
