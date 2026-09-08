"""Diffusion thermique 2D — schéma explicite (FTCS) vectorisé numpy, pas de temps stable CFL.

Modèle :
  - grille cartésienne (cell_size_mm), sources = composants.power_w réparties
    uniformément sur les cellules de leur empreinte ;
  - conductivité locale accrue autour des vias thermiques (facteur VIA_BOOST) ;
  - convection surfacique (condition de Robin) appliquée aux deux faces de la
    carte — les bords de carte eux-mêmes restent adiabatiques (halos répliqués) ;
  - dt borné par la condition CFL : dt ≤ CFL·dx²/(4·α_max).

Le même stencil est implémenté côté CUDA (kernels/thermal_kernel.cu, api C
`thermal_step_cuda` appelable par ctypes). Si la librairie est compilée et
ENABLE_CUDA actif, la boucle temporelle est déléguée au GPU ; sinon (et en cas
de moindre erreur), le schéma numpy prend le relais — le résultat est identique
car les coefficients par cellule (c_int, c_src, c_conv) sont calculés une seule
fois en numpy et partagés entre les deux backends.
"""

from __future__ import annotations

import ctypes
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

from common.config import get_settings
from common.log import get_logger

logger = get_logger("simulator.thermal_sim")

# cupy import GARDÉ — fallback numpy si cupy absent (cible : GPU optionnel)
try:  # pragma: no cover - dépend de l'environnement
    import cupy as _cp  # type: ignore

    HAS_CUPY = True
except Exception:  # ImportError ou driver absent
    _cp = None
    HAS_CUPY = False

# ---- paramètres matériau par défaut (documentés, ordres de grandeur réalistes) ----
RHO_CP_DEFAULT = 2.0e6        # J/(m³·K) — capacité thermique volumique effective FR4 + cuivre
ALPHA_DEFAULT = 5.0e-6        # m²/s — diffusivité thermique effective (plans cuivre inclus)
VIA_BOOST = 8.0               # multiplicateur de diffusivité locale autour d'un via thermique
H_CONV_DEFAULT = 100.0        # W/(m²·K) — convection par face (forcée + étalement cuivre)
BOARD_THICKNESS_MM = 1.6      # épaisseur standard 4 couches
T_AMB_DEFAULT = 25.0          # °C
HOTSPOT_DELTA_C = 8.0         # un hotspot est signalé au-delà de T_amb + 8 K
CFL = 0.9                     # marge de sécurité sous la limite dt ≤ dx²/(4·α_max)


# --------------------------------------------------------------------------- #
#  Résultats
# --------------------------------------------------------------------------- #
@dataclass
class Hotspot:
    """Zone chaude détectée — publiée sur le constraint_bus (THERMAL_ZONE_UPDATE)."""

    x_min_mm: float
    y_min_mm: float
    x_max_mm: float
    y_max_mm: float
    temp_c: float
    component_ref: str = ""
    kind: str = "component"          # component | zone
    limit_c: Optional[float] = None  # consigne dépassée (zones thermiques déclarées)

    def to_dict(self) -> dict:
        return {
            "x_min_mm": self.x_min_mm, "y_min_mm": self.y_min_mm,
            "x_max_mm": self.x_max_mm, "y_max_mm": self.y_max_mm,
            "temp_c": self.temp_c, "component_ref": self.component_ref,
            "kind": self.kind, "limit_c": self.limit_c,
        }


@dataclass
class ThermalResult:
    """Résultat thermique complet (carte de température + hotspots)."""

    max_temp_c: float
    ambient_c: float
    hotspots: List[Hotspot] = field(default_factory=list)
    cell_size_mm: float = 1.0
    grid_nx: int = 0
    grid_ny: int = 0
    iterations: int = 0
    elapsed_s: float = 0.0
    sim_time_s: float = 0.0
    converged: bool = False
    backend: str = "numpy"
    via_count: int = 0
    temperature_map: Optional[np.ndarray] = None  # exclue de to_dict (tableau brut)

    def to_dict(self) -> dict:
        return {
            "max_temp_c": round(self.max_temp_c, 2),
            "ambient_c": self.ambient_c,
            "hotspots": [h.to_dict() for h in self.hotspots],
            "cell_size_mm": self.cell_size_mm,
            "grid_nx": self.grid_nx, "grid_ny": self.grid_ny,
            "iterations": self.iterations,
            "elapsed_s": round(self.elapsed_s, 4),
            "sim_time_s": round(self.sim_time_s, 3),
            "converged": self.converged,
            "backend": self.backend,
            "via_count": self.via_count,
        }


# --------------------------------------------------------------------------- #
#  Binding ctypes du noyau CUDA compilé (api C stable — voir kernels/README.md)
# --------------------------------------------------------------------------- #
class CudaThermalKernel:
    """Wrapper ctypes autour de `thermal_step_cuda` (libthermal_kernel.so)."""

    def __init__(self, lib_path: Path) -> None:
        lib = ctypes.CDLL(str(lib_path))
        # int thermal_step_cuda(const float* temp, const float* c_int, const float* c_src,
        #                       float* out, int nx, int ny, float t_amb, float c_conv)
        fn = lib.thermal_step_cuda
        fn.argtypes = [
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
            ctypes.c_int, ctypes.c_int, ctypes.c_float, ctypes.c_float,
        ]
        fn.restype = ctypes.c_int
        avail = lib.thermal_available
        avail.restype = ctypes.c_int
        if avail() != 1:
            raise RuntimeError("kernel CUDA présent mais GPU indisponible")
        self._lib = lib
        self._step_fn = fn
        self.path = str(lib_path)

    def step(
        self, temp: np.ndarray, c_int: np.ndarray, c_src: np.ndarray,
        out: np.ndarray, t_amb: float, c_conv: float,
    ) -> None:
        """Un pas FTCS sur GPU (tableaux float32 C-contigus)."""
        nx, ny = temp.shape[1], temp.shape[0]
        rc = self._step_fn(
            temp.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            c_int.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            c_src.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            nx, ny, ctypes.c_float(t_amb), ctypes.c_float(c_conv),
        )
        if rc != 0:
            raise RuntimeError(f"thermal_step_cuda a retourné l'erreur {rc}")


def _candidate_lib_paths() -> List[Path]:
    """Chemins candidats de la librairie compilée (kernels/build/...)."""
    build = Path(__file__).resolve().parents[1] / "kernels" / "build"
    names = [
        "libthermal_kernel.so", "libthermal_kernel.dylib",
        "thermal_kernel.dll", "Release/thermal_kernel.dll",
    ]
    return [build / n for n in names]


def try_load_cuda_kernel(enable_cuda: Optional[bool] = None) -> Optional[CudaThermalKernel]:
    """Tente le binding ctypes ; retourne None si absent/désactivé (fallback numpy).

    Mémoïsé — la détection ne fait aucun appel réseau ni compilation.
    """
    global _CUDA_KERNEL_CACHE
    if enable_cuda is None:
        enable_cuda = bool(get_settings("simulator").enable_cuda)
    if not enable_cuda:
        return None
    if _CUDA_KERNEL_CACHE is not None:
        return _CUDA_KERNEL_CACHE or None
    for path in _candidate_lib_paths():
        if path.exists():
            try:
                kernel = CudaThermalKernel(path)
                logger.info("noyau thermique CUDA chargé", extra={"lib": str(path)})
                _CUDA_KERNEL_CACHE = kernel
                return kernel
            except Exception as exc:  # lib présente mais GPU/API incompatible
                logger.warning("chargement du noyau CUDA échoué — fallback numpy",
                               extra={"lib": str(path), "error": str(exc)})
                break
    _CUDA_KERNEL_CACHE = False
    return None


_CUDA_KERNEL_CACHE: "Optional[CudaThermalKernel] | bool" = None


# --------------------------------------------------------------------------- #
#  Construction du maillage
# --------------------------------------------------------------------------- #
def _cell_region(
    x0: float, y0: float, x1: float, y1: float, cell: float, nx: int, ny: int,
) -> Tuple[int, int, int, int]:
    """Convertit un rectangle mm → indices de cellules inclusifs (col0, col1, row0, row1)."""
    c0 = int(np.clip(math.floor(x0 / cell), 0, nx - 1))
    c1 = int(np.clip(math.ceil(x1 / cell) - 1, 0, nx - 1))
    r0 = int(np.clip(math.floor(y0 / cell), 0, ny - 1))
    r1 = int(np.clip(math.ceil(y1 / cell) - 1, 0, ny - 1))
    return c0, c1, r0, r1


@dataclass
class _Mesh:
    """Maillage préparé : diffusivité, sources, rectangles de composants."""

    nx: int
    ny: int
    cell_mm: float
    alpha: np.ndarray            # diffusivité locale (m²/s), boostée par les vias
    src_power: np.ndarray        # W par cellule
    comp_cells: Dict[str, Tuple[int, int, int, int]]
    via_count: int


def _build_mesh(board, cell_size_mm: float) -> _Mesh:
    """Maillage : sources = power_w réparties sur les cellules de l'empreinte ; vias = boost α."""
    nx = max(4, int(round(board.width_mm / cell_size_mm)))
    ny = max(4, int(round(board.height_mm / cell_size_mm)))
    alpha = np.full((ny, nx), ALPHA_DEFAULT, dtype=np.float64)
    src_power = np.zeros((ny, nx), dtype=np.float64)
    comp_cells: Dict[str, Tuple[int, int, int, int]] = {}

    for ref, comp in board.components.items():
        placement = board.placements.get(ref)
        if placement is None:
            continue
        x0, y0, x1, y1 = comp.bounding_box(placement)
        x0 = max(0.0, x0); y0 = max(0.0, y0)
        x1 = min(board.width_mm, x1); y1 = min(board.height_mm, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        c0, c1, r0, r1 = _cell_region(x0, y0, x1, y1, cell_size_mm, nx, ny)
        comp_cells[ref] = (c0, c1, r0, r1)
        if comp.power_w > 0:
            n_cells = (c1 - c0 + 1) * (r1 - r0 + 1)
            src_power[r0:r1 + 1, c0:c1 + 1] += comp.power_w / n_cells

    # vias thermiques : un via concentre la chaleur entre couches → conductivité locale accrue
    via_count = 0
    for net in board.nets.values():
        for seg in net.routed_segments:
            if not seg.is_via:
                continue
            via_count += 1
            c0, c1, r0, r1 = _cell_region(
                seg.x1_mm - cell_size_mm, seg.y1_mm - cell_size_mm,
                seg.x1_mm + cell_size_mm, seg.y1_mm + cell_size_mm,
                cell_size_mm, nx, ny,
            )
            alpha[r0:r1 + 1, c0:c1 + 1] *= VIA_BOOST

    return _Mesh(nx=nx, ny=ny, cell_mm=cell_size_mm, alpha=alpha,
                 src_power=src_power, comp_cells=comp_cells, via_count=via_count)


def _timestep(mesh: _Mesh, dx_m: float) -> float:
    """Pas de temps CFL-stable : dt ≤ CFL·dx²/(4·α_max)."""
    return CFL * dx_m * dx_m / (4.0 * float(mesh.alpha.max()))


def _coefficients(mesh: _Mesh, dt: float, dx_m: float, h_conv: float, thickness_m: float):
    """Coefficients par cellule partagés numpy/CUDA :
    c_int (conduction, sans unité), c_src (K par pas), c_conv (convection des deux
    faces, sans unité) — condition de Robin uniforme."""
    c_int = (mesh.alpha * dt) / (dx_m * dx_m)
    cell_volume = thickness_m * dx_m * dx_m
    c_src = (mesh.src_power * dt) / (RHO_CP_DEFAULT * cell_volume)
    c_conv = (2.0 * h_conv * dt) / (RHO_CP_DEFAULT * thickness_m)  # deux faces
    return c_int, c_src, c_conv


def _step_numpy(
    T: np.ndarray, c_int: np.ndarray, c_src: np.ndarray, c_conv: float, t_amb: float,
) -> np.ndarray:
    """Un pas FTCS vectorisé — halos adiabatiques (mode edge) + convection surfacique."""
    padded = np.pad(T, 1, mode="edge")
    lap = (padded[2:, 1:-1] + padded[:-2, 1:-1]
           + padded[1:-1, 2:] + padded[1:-1, :-2] - 4.0 * T)
    # condition de Robin : refroidissement des deux faces vers l'ambiant
    return T + c_int * lap + c_src + c_conv * (t_amb - T)


def _resolve_backend(backend: str, enable_cuda: Optional[bool]):
    """Choix du backend thermique : 'auto' → ctypes CUDA si présent, sinon numpy.

    cupy est gardé en import (voir en-tête de module) mais réservé aux calculs
    de rastérisation (em_sim) : le stencil thermique tire profit du kernel CUDA
    natif ou de numpy — jamais d'un stencil cupy interprété cellule par cellule.
    """
    if backend in ("cuda", "auto"):
        kernel = try_load_cuda_kernel(enable_cuda)
        if kernel is not None:
            return kernel, "cuda:libthermal_kernel"
        if backend == "cuda":
            logger.warning("backend cuda demandé mais libthermal_kernel absent — fallback numpy")
    return None, "numpy"


# --------------------------------------------------------------------------- #
#  Détection des hotspots
# --------------------------------------------------------------------------- #
def _detect_hotspots(
    T: np.ndarray, mesh: _Mesh, board, t_amb: float,
    threshold_delta_c: float = HOTSPOT_DELTA_C,
) -> List[Hotspot]:
    """Hotspots par composant dissipateur + violations des zones thermiques déclarées."""
    cell = mesh.cell_mm
    hotspots: List[Hotspot] = []

    for ref, (c0, c1, r0, r1) in mesh.comp_cells.items():
        comp = board.components[ref]
        if comp.power_w <= 0:
            continue
        tmax = float(T[r0:r1 + 1, c0:c1 + 1].max())
        if tmax >= t_amb + threshold_delta_c:
            hotspots.append(Hotspot(
                x_min_mm=round(c0 * cell, 2), y_min_mm=round(r0 * cell, 2),
                x_max_mm=round((c1 + 1) * cell, 2), y_max_mm=round((r1 + 1) * cell, 2),
                temp_c=round(tmax, 2), component_ref=ref, kind="component",
            ))

    # zones thermiques déclarées sur la carte (consigne max_temp_c)
    for zone in getattr(board, "zones", []):
        if zone.kind != "thermal" or zone.max_temp_c is None:
            continue
        c0, c1, r0, r1 = _cell_region(
            zone.x_min_mm, zone.y_min_mm, zone.x_max_mm, zone.y_max_mm, cell, mesh.nx, mesh.ny,
        )
        tmax = float(T[r0:r1 + 1, c0:c1 + 1].max())
        if tmax > zone.max_temp_c:
            hotspots.append(Hotspot(
                x_min_mm=zone.x_min_mm, y_min_mm=zone.y_min_mm,
                x_max_mm=zone.x_max_mm, y_max_mm=zone.y_max_mm,
                temp_c=round(tmax, 2), component_ref="", kind="zone",
                limit_c=zone.max_temp_c,
            ))

    hotspots.sort(key=lambda h: -h.temp_c)
    return hotspots


# --------------------------------------------------------------------------- #
#  API publique
# --------------------------------------------------------------------------- #
def steady_state(
    board,
    cell_size_mm: float = 1.0,
    t_amb_c: float = T_AMB_DEFAULT,
    h_conv: float = H_CONV_DEFAULT,
    thickness_mm: float = BOARD_THICKNESS_MM,
    max_iter: int = 20000,
    tol_c: float = 1e-3,
    backend: str = "auto",
    enable_cuda: Optional[bool] = None,
) -> ThermalResult:
    """Régime permanent : itère jusqu'à stabilisation (ΔT max < tol_c) ou max_iter.

    Retourne la carte de température et la liste des hotspots (composants et
    zones thermiques violées) — consommée par le multi_physics_loop et le
    service gRPC simulator.
    """
    t0 = time.perf_counter()
    mesh = _build_mesh(board, cell_size_mm)
    dx_m = cell_size_mm * 1e-3
    dt = _timestep(mesh, dx_m)
    c_int, c_src, c_conv = _coefficients(mesh, dt, dx_m, h_conv, thickness_mm * 1e-3)
    c_int32 = c_int.astype(np.float32)
    c_src32 = c_src.astype(np.float32)

    kernel, backend_name = _resolve_backend(backend, enable_cuda)
    T = np.full((mesh.ny, mesh.nx), t_amb_c, dtype=np.float64)
    T32 = T.astype(np.float32)
    out32 = np.empty_like(T32)

    iterations = 0
    converged = False
    if isinstance(kernel, CudaThermalKernel):
        # boucle GPU — double buffer via tableaux ctypes (float32, C-contigus)
        for iterations in range(1, max_iter + 1):
            kernel.step(T32, c_int32, c_src32, out32, t_amb_c, c_conv)
            delta = float(np.abs(out32 - T32).max())
            T32, out32 = out32, T32
            if iterations % 25 == 0 and delta < tol_c:
                converged = True
                break
        T = T32.astype(np.float64)
    else:
        for iterations in range(1, max_iter + 1):
            Tn = _step_numpy(T, c_int, c_src, c_conv, t_amb_c)
            delta = float(np.abs(Tn - T).max())
            T = Tn
            if iterations % 25 == 0 and delta < tol_c:
                converged = True
                break

    hotspots = _detect_hotspots(T, mesh, board, t_amb_c)
    return ThermalResult(
        max_temp_c=round(float(T.max()), 2),
        ambient_c=t_amb_c,
        hotspots=hotspots,
        cell_size_mm=cell_size_mm,
        grid_nx=mesh.nx, grid_ny=mesh.ny,
        iterations=iterations,
        elapsed_s=round(time.perf_counter() - t0, 4),
        sim_time_s=round(iterations * dt, 3),
        converged=converged,
        backend=backend_name,
        via_count=mesh.via_count,
        temperature_map=T,
    )


def transient(
    board,
    t_s: float,
    frames: int = 8,
    cell_size_mm: float = 1.0,
    t_amb_c: float = T_AMB_DEFAULT,
    h_conv: float = H_CONV_DEFAULT,
    thickness_mm: float = BOARD_THICKNESS_MM,
    backend: str = "auto",
    enable_cuda: Optional[bool] = None,
) -> Iterator[ThermalResult]:
    """Transitoire sur t_s secondes — générateur de résultats partiels (streaming gRPC).

    Yield ~`frames` ThermalResult intermédiaires (converged=False) puis l'état
    final. Utilisé par Simulator.run(physics="thermal", streaming=True).
    """
    mesh = _build_mesh(board, cell_size_mm)
    dx_m = cell_size_mm * 1e-3
    dt = _timestep(mesh, dx_m)
    c_int, c_src, c_conv = _coefficients(mesh, dt, dx_m, h_conv, thickness_mm * 1e-3)
    kernel, backend_name = _resolve_backend(backend, enable_cuda)

    T = np.full((mesh.ny, mesh.nx), t_amb_c, dtype=np.float64)
    total_steps = max(1, int(round(t_s / dt)))
    snapshot_every = max(1, total_steps // max(1, frames))
    t_start = time.perf_counter()

    for step_idx in range(1, total_steps + 1):
        if isinstance(kernel, CudaThermalKernel):
            c_int32 = c_int.astype(np.float32)
            c_src32 = c_src.astype(np.float32)
            T32 = T.astype(np.float32)
            out32 = np.empty_like(T32)
            kernel.step(T32, c_int32, c_src32, out32, t_amb_c, c_conv)
            T = out32.astype(np.float64)
        else:
            T = _step_numpy(T, c_int, c_src, c_conv, t_amb_c)
        if step_idx % snapshot_every == 0 or step_idx == total_steps:
            yield ThermalResult(
                max_temp_c=round(float(T.max()), 2),
                ambient_c=t_amb_c,
                hotspots=_detect_hotspots(T, mesh, board, t_amb_c),
                cell_size_mm=cell_size_mm,
                grid_nx=mesh.nx, grid_ny=mesh.ny,
                iterations=step_idx,
                elapsed_s=round(time.perf_counter() - t_start, 4),
                sim_time_s=round(step_idx * dt, 3),
                converged=(step_idx == total_steps),
                backend=backend_name,
                via_count=mesh.via_count,
                temperature_map=T,
            )
