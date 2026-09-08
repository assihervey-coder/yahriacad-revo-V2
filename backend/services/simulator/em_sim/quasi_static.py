"""Analyse électromagnétique quasi-statique — diaphonie, EMI, intégrité du plan de masse.

Hypothèses (calibration heuristique, ordres de grandeur physiques) :
  - couplage capacitif entre segments parallèles : C ≈ ε₀·ε_r·A/d avec
    A = longueur de recouvrement × largeur moyenne, d = distance perpendiculaire ;
  - diaphonie en dB : 20·log₁₀(C_c / (C_self + C_c)) par paire de nets ;
  - rayonnement EMI ∝ aire de boucle du courant de retour (polygone fermé par
    la chaîne de segments d'un net) ;
  - rebond de masse ∝ ratio de découpages du plan de masse × activité des
    classes rapides.

Tout est vectorisé numpy (cupy import gardé, utilisé si présent et ENABLE_CUDA)
; un noyau C++ OpenMP (kernels/em_kernel.cpp, api C `em_coupling_matrix`) prend
le relais via ctypes s'il est compilé.
"""

from __future__ import annotations

import ctypes
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from common.config import get_settings
from common.log import get_logger

logger = get_logger("simulator.em_sim")

# cupy import GARDÉ — fallback numpy si absent
try:  # pragma: no cover - dépend de l'environnement
    import cupy as _cp  # type: ignore

    HAS_CUPY = True
except Exception:
    _cp = None
    HAS_CUPY = False

EPS_0 = 8.8541878128e-12      # F/m — permittivité du vide
EPS_R_DEFAULT = 4.2           # ε_r FR4
DIEL_THICKNESS_MM = 0.2       # hauteur diélectrique piste→plan de masse
GROUND_PLANE_LAYER = 1        # couche du plan de masse (convention Board: layer 1 = GND)
NO_COUPLING_DB = -99.0        # sentinelle « aucun couplage détecté »
LOOP_AREA_WARN_MM2 = 25.0     # seuil d'alerte aire de boucle
LOOP_AREA_CRITICAL_MM2 = 80.0

# proxy d'activité di/dt par classe de net (normalisé, heuristique EMI)
DI_DT_PROXY: Dict[str, float] = {
    "usb3": 1.0, "pcie": 1.0, "ddr4": 0.9, "mipi": 0.8,
    "usb2": 0.5, "ethernet": 0.5, "high_speed": 0.7,
    "diff": 0.5, "default": 0.15, "power": 0.2,
}


# --------------------------------------------------------------------------- #
#  Résultats
# --------------------------------------------------------------------------- #
@dataclass
class CouplingPair:
    """Couplage capacitif agrégé entre deux nets (tous segments confondus)."""

    net_a: str
    net_b: str
    coupling_pf: float
    distance_mm: float
    overlap_mm: float

    def to_dict(self) -> dict:
        return {
            "net_a": self.net_a, "net_b": self.net_b,
            "coupling_pf": round(self.coupling_pf, 4),
            "distance_mm": round(self.distance_mm, 2),
            "overlap_mm": round(self.overlap_mm, 2),
        }


@dataclass
class EMIZone:
    """Zone rectangulaire émettrice (boucle de courant) — publiée comme KEEPOUT_ZONE."""

    x_min_mm: float
    y_min_mm: float
    x_max_mm: float
    y_max_mm: float
    net: str
    loop_area_mm2: float
    severity: str  # info | warning | critical

    def to_dict(self) -> dict:
        return {
            "x_min_mm": self.x_min_mm, "y_min_mm": self.y_min_mm,
            "x_max_mm": self.x_max_mm, "y_max_mm": self.y_max_mm,
            "net": self.net, "loop_area_mm2": round(self.loop_area_mm2, 2),
            "severity": self.severity,
        }


@dataclass
class EMResult:
    """Résultat électromagnétique quasi-statique (contrat proto EMResult)."""

    crosstalk_max_db: float
    ground_bounce_mv: float
    emi_zones: List[EMIZone] = field(default_factory=list)
    coupling_pairs: List[CouplingPair] = field(default_factory=list)
    ground_void_ratio: float = 0.0
    worst_pair: Optional[CouplingPair] = None
    elapsed_s: float = 0.0
    backend: str = "numpy"

    def to_dict(self) -> dict:
        return {
            "crosstalk_max_db": round(self.crosstalk_max_db, 2),
            "ground_bounce_mv": round(self.ground_bounce_mv, 2),
            "emi_zones": [z.to_dict() for z in self.emi_zones],
            "coupling_pairs": [p.to_dict() for p in self.coupling_pairs[:8]],
            "ground_void_ratio": round(self.ground_void_ratio, 4),
            "worst_pair": self.worst_pair.to_dict() if self.worst_pair else None,
            "elapsed_s": round(self.elapsed_s, 4),
            "backend": self.backend,
        }


# --------------------------------------------------------------------------- #
#  Binding ctypes du noyau C++ OpenMP (api C — voir kernels/README.md)
# --------------------------------------------------------------------------- #
def try_load_em_kernel(use_native: Optional[bool] = None):
    """Charge libem_kernel.so s'il est compilé (matrice de couplage OpenMP).

    Politique : tentative uniquement si `use_native` est vrai (ou ENABLE_CUDA
    actif — la présence du socle natif est pilotée par la même variable) ;
    sinon None → boucles numpy. Aucun appel réseau, échec silencieux.
    """
    if use_native is None:
        use_native = bool(get_settings("simulator").enable_cuda)
    if not use_native:
        return None
    build = Path(__file__).resolve().parents[1] / "kernels" / "build"
    for name in ("libem_kernel.so", "libem_kernel.dylib", "em_kernel.dll"):
        path = build / name
        if not path.exists():
            continue
        try:
            lib = ctypes.CDLL(str(path))
            fn = lib.em_coupling_matrix
            # int em_coupling_matrix(const double* segs, const int* net_ids, int n_seg,
            #                        int n_nets, double eps_r, double* c_matrix_pf)
            fn.argtypes = [
                ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_int),
                ctypes.c_int, ctypes.c_int, ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
            ]
            fn.restype = ctypes.c_int
            logger.info("noyau EM natif chargé", extra={"lib": str(path)})
            return fn
        except Exception as exc:  # pragma: no cover - environnement sans GPU/toolchain
            logger.warning("chargement libem_kernel échoué — fallback numpy",
                           extra={"lib": str(path), "error": str(exc)})
            return None
    return None


# --------------------------------------------------------------------------- #
#  Géométrie : distance / recouvrement entre segments parallèles
# --------------------------------------------------------------------------- #
def _segment_arrays(segments) -> Tuple[np.ndarray, np.ndarray]:
    """(N,4) extrémités + (N,) largeurs pour les segments non-via."""
    pts = np.array(
        [[s.x1_mm, s.y1_mm, s.x2_mm, s.y2_mm] for s in segments], dtype=np.float64,
    ).reshape(-1, 4)
    widths = np.array([s.width_mm for s in segments], dtype=np.float64)
    return pts, widths


def _parallel_metrics(a: np.ndarray, b: np.ndarray) -> Tuple[bool, float, float]:
    """Pour deux segments : (parallèles ?, distance perpendiculaire mm, recouvrement mm)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ua = np.array([ax2 - ax1, ay2 - ay1])
    ub = np.array([bx2 - bx1, by2 - by1])
    la = float(np.linalg.norm(ua))
    lb = float(np.linalg.norm(ub))
    if la < 1e-9 or lb < 1e-9:
        return False, 0.0, 0.0
    u = ua / la
    sin_theta = abs(ua[0] * ub[1] - ua[1] * ub[0]) / (la * lb)
    if sin_theta > 0.05:  # tolérance « quasi-parallèle »
        return False, 0.0, 0.0
    # distance perpendiculaire du milieu de b à la droite portée par a
    mb = np.array([(bx1 + bx2) / 2.0, (by1 + by2) / 2.0])
    ma = np.array([(ax1 + ax2) / 2.0, (ay1 + ay2) / 2.0])
    perp = abs(ua[0] * (mb[1] - ma[1]) - ua[1] * (mb[0] - ma[0])) / la
    # recouvrement des projections sur l'axe de a
    ta = np.array([np.dot(np.array([bx1, by1]) - ma, u), np.dot(np.array([bx2, by2]) - ma, u)])
    overlap = min(la / 2.0, ta.max()) - max(-la / 2.0, ta.min())
    return True, float(perp), float(max(0.0, overlap))


def _loop_area_mm2(segments: List) -> Tuple[float, Tuple[float, float, float, float]]:
    """Aire du polygone fermé par la chaîne de segments (shoelace) + bbox.

    Les segments sont chaînés gloutonnement depuis le premier (ordre du
    routeur), puis refermés — approximation de l'aire de boucle de courant.
    """
    if not segments:
        return 0.0, (0.0, 0.0, 0.0, 0.0)
    pts: List[Tuple[float, float]] = [(segments[0].x1_mm, segments[0].y1_mm)]
    used = [False] * len(segments)
    used[0] = True
    cur = (segments[0].x2_mm, segments[0].y2_mm)
    for _ in range(len(segments) - 1):
        best_i, best_d = -1, float("inf")
        for i, seg in enumerate(segments):
            if used[i]:
                continue
            for p in ((seg.x1_mm, seg.y1_mm), (seg.x2_mm, seg.y2_mm)):
                d = math.hypot(p[0] - cur[0], p[1] - cur[1])
                if d < best_d:
                    best_d, best_i = d, i
        if best_i < 0:
            break
        seg = segments[best_i]
        used[best_i] = True
        # prolonger la chaîne par l'extrémité la plus proche
        d1 = math.hypot(seg.x1_mm - cur[0], seg.y1_mm - cur[1])
        d2 = math.hypot(seg.x2_mm - cur[0], seg.y2_mm - cur[1])
        if d1 <= d2:
            pts.append((seg.x1_mm, seg.y1_mm))
            cur = (seg.x2_mm, seg.y2_mm)
        else:
            pts.append((seg.x2_mm, seg.y2_mm))
            cur = (seg.x1_mm, seg.y1_mm)
    pts.append(cur)
    arr = np.array(pts, dtype=np.float64)
    x, y = arr[:, 0], arr[:, 1]
    area = abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2.0
    bbox = (float(x.min()), float(y.min()), float(x.max()), float(y.max()))
    return area, bbox


def _occupancy_grid(segments, cell_mm: float, nx: int, ny: int, xp) -> np.ndarray:
    """Densité de cuivre par cellule (rastérisation vectorisée) — xp = numpy ou cupy."""
    if not segments:
        return xp.zeros((ny, nx), dtype=np.float64)
    pts, widths = _segment_arrays(segments)
    xs = (pts[:, [0, 2]] / cell_mm).clip(0, nx - 1e-6)
    ys = (pts[:, [1, 3]] / cell_mm).clip(0, ny - 1e-6)
    lengths = xp.hypot(xs[:, 1] - xs[:, 0], ys[:, 1] - ys[:, 0])
    grid = xp.zeros((ny, nx), dtype=np.float64)
    # échantillonnage vectorisé : 8 points par segment
    t = xp.linspace(0.0, 1.0, 8).reshape(1, -1)
    sx = xs[:, 0:1] + t * (xs[:, 1:2] - xs[:, 0:1])
    sy = ys[:, 0:1] + t * (ys[:, 1:2] - ys[:, 0:1])
    ix = sx.astype(xp.int64).reshape(-1)
    iy = sy.astype(xp.int64).reshape(-1)
    contrib = xp.repeat((lengths * widths) / 8.0, 8)
    if xp is np:  # numpy : accumulation vectorisée ; cupy : boucle d'index (pas d'add.at)
        np.add.at(grid, (iy, ix), contrib)
    else:  # pragma: no cover - cupy absent dans l'environnement de dev
        for i, j, w in zip(iy.tolist(), ix.tolist(), contrib.tolist()):
            grid[i, j] += w
    return grid


# --------------------------------------------------------------------------- #
#  API principale
# --------------------------------------------------------------------------- #
def quasi_static_analysis(
    board,
    eps_r: float = EPS_R_DEFAULT,
    parallel_max_dist_mm: float = 3.0,
    min_overlap_mm: float = 0.5,
    use_native: Optional[bool] = None,
) -> EMResult:
    """Analyse quasi-statique complète → EMResult (contrat proto pcb.simulator.v1).

    1. matrice de couplage capacitif nets↔nets (kernel C++ si présent, sinon numpy) ;
    2. diaphonie max par paire de nets : 20·log₁₀(C_c/(C_self+C_c)) ;
    3. aires de boucles par net → zones EMI hiérarchisées ;
    4. intégrité du plan de masse (découpages + densité de pistes) → rebond de masse.
    """
    t0 = time.perf_counter()
    xp = _cp if (HAS_CUPY and get_settings("simulator").enable_cuda) else np

    # --- 1. segments par net -------------------------------------------------
    nets_segments: Dict[str, List] = {
        name: [s for s in net.routed_segments if not s.is_via]
        for name, net in board.nets.items() if net.routed_segments
    }
    names = sorted(nets_segments)
    flat = [seg for name in names for seg in nets_segments[name]]
    net_ids = np.array(
        [i for i, name in enumerate(names) for _ in nets_segments[name]], dtype=np.int32,
    )

    coupling_pairs: List[CouplingPair] = []
    worst_ratio = 0.0
    worst_pair: Optional[CouplingPair] = None

    if flat:
        pts, widths = _segment_arrays(flat)
        native_fn = try_load_em_kernel(use_native)
        matrix_pf = None
        if native_fn is not None:
            try:
                segs_c = np.ascontiguousarray(
                    np.column_stack([pts, widths]).astype(np.float64).ravel()
                )
                n_nets = max(1, len(names))
                out = np.zeros(n_nets * n_nets, dtype=np.float64)
                rc = native_fn(
                    segs_c.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                    net_ids.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
                    len(flat), n_nets, ctypes.c_double(eps_r),
                    out.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                )
                if rc == 0:
                    matrix_pf = out.reshape(n_nets, n_nets)
            except Exception as exc:  # pragma: no cover
                logger.warning("em_kernel natif en erreur — fallback numpy", extra={"error": str(exc)})
                matrix_pf = None

        if matrix_pf is not None:
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    c_pf = float(matrix_pf[i, j])
                    if c_pf > 0:
                        coupling_pairs.append(CouplingPair(names[i], names[j], c_pf, 0.0, 0.0))
        else:
            # --- couplage par paires de segments (numpy, vectorisé par paire de nets) ---
            for i in range(len(names)):
                segs_a = nets_segments[names[i]]
                for j in range(i + 1, len(names)):
                    segs_b = nets_segments[names[j]]
                    c_total = 0.0
                    d_min, ov_max = float("inf"), 0.0
                    for sa, wa in zip(segs_a, _segment_arrays(segs_a)[1]):
                        pa, _ = _segment_arrays([sa])
                        for sb, wb in zip(segs_b, _segment_arrays(segs_b)[1]):
                            pb, _ = _segment_arrays([sb])
                            parallel, dist, overlap = _parallel_metrics(pa[0], pb[0])
                            if not parallel or overlap < min_overlap_mm:
                                continue
                            if dist > parallel_max_dist_mm or dist < 1e-6:
                                continue
                            area_m2 = (overlap * 1e-3) * ((wa + wb) / 2.0 * 1e-3)
                            d_m = dist * 1e-3
                            c_total += EPS_0 * eps_r * area_m2 / d_m * 1e12  # pF
                            d_min = min(d_min, dist)
                            ov_max = max(ov_max, overlap)
                    if c_total > 0:
                        coupling_pairs.append(CouplingPair(
                            names[i], names[j], float(c_total),
                            d_min if math.isfinite(d_min) else 0.0, ov_max,
                        ))

        # --- 2. diaphonie : ratio capacitive vs capacité propre ------------------
        self_cap_pf: Dict[str, float] = {}
        for name in names:
            segs = nets_segments[name]
            _, w = _segment_arrays(segs)
            lengths = np.array([s.length_mm for s in segs])
            # capacité propre au plan : C ≈ ε·L·w / h_diélectrique
            self_cap_pf[name] = float(
                np.sum(EPS_0 * eps_r * (lengths * 1e-3) * (w * 1e-3) / (DIEL_THICKNESS_MM * 1e-3) * 1e12)
            )
        for pair in coupling_pairs:
            c_victim = min(self_cap_pf.get(pair.net_a, 1.0), self_cap_pf.get(pair.net_b, 1.0))
            pair_db = 20.0 * math.log10(pair.coupling_pf / (c_victim + pair.coupling_pf))
            ratio = pair.coupling_pf / (c_victim + pair.coupling_pf)
            if ratio > worst_ratio:
                worst_ratio = ratio
                worst_pair = pair

    # --- 3. aires de boucles → zones EMI -------------------------------------
    emi_zones: List[EMIZone] = []
    for name in names:
        area, bbox = _loop_area_mm2(nets_segments[name])
        if area <= LOOP_AREA_WARN_MM2:
            continue
        severity = "critical" if area >= LOOP_AREA_CRITICAL_MM2 else "warning"
        emi_zones.append(EMIZone(
            x_min_mm=bbox[0], y_min_mm=bbox[1], x_max_mm=bbox[2], y_max_mm=bbox[3],
            net=name, loop_area_mm2=area, severity=severity,
        ))

    # --- 4. intégrité du plan de masse ---------------------------------------
    board_area = board.width_mm * board.height_mm
    void_area = 0.0
    for zone in getattr(board, "zones", []):
        if zone.kind in ("keepout", "cutout"):
            w = max(0.0, zone.x_max_mm - zone.x_min_mm)
            h = max(0.0, zone.y_max_mm - zone.y_min_mm)
            void_area += w * h
    void_ratio = min(1.0, void_area / board_area) if board_area > 0 else 0.0
    # densité de pistes sur la couche GND (découpage effectif additionnel)
    ground_segments = [
        s for net in board.nets.values() for s in net.routed_segments
        if not s.is_via and s.layer == GROUND_PLANE_LAYER
    ]
    if ground_segments and board_area > 0:
        grid = _occupancy_grid(ground_segments, 1.0, int(board.width_mm), int(board.height_mm), xp)
        density = float(grid.mean()) if xp is np else float(grid.get().mean())  # type: ignore[union-attr]
        void_ratio = min(1.0, void_ratio + density * 0.5)

    activity = sum(
        DI_DT_PROXY.get(net.net_class.lower(), DI_DT_PROXY["default"])
        for net in board.nets.values() if net.is_routed
    )
    # heuristique calibrée : rebond = 40 mV × découpage × (1 + Σ proxies di/dt)
    ground_bounce_mv = 40.0 * void_ratio * (1.0 + activity)

    crosstalk_db = NO_COUPLING_DB
    if worst_ratio > 0:
        crosstalk_db = 20.0 * math.log10(worst_ratio)

    return EMResult(
        crosstalk_max_db=round(crosstalk_db, 2),
        ground_bounce_mv=round(ground_bounce_mv, 2),
        emi_zones=emi_zones,
        coupling_pairs=sorted(coupling_pairs, key=lambda p: -p.coupling_pf),
        ground_void_ratio=void_ratio,
        worst_pair=worst_pair,
        elapsed_s=round(time.perf_counter() - t0, 4),
        backend="cupy" if xp is not np else "numpy",
    )
