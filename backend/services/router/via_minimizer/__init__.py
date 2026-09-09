"""Réduction de vias — brique DeepPCB (objectif −44 %, section 6.4)."""

from .minimizer import MinimizerStats, ViaMinimizer

__all__ = ["ViaMinimizer", "MinimizerStats", "TARGET_REDUCTION_PCT"]
