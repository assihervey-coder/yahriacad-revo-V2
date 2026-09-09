"""Sous-système 5.5 — optimiseur autonome [AutoPCB] : boucle ratchet nocturne.

Pipeline par itération : proposer (heuristiques déterministes + hook LLM
optionnel) → évaluer (< 5 s, budget ``fast_eval_budget_s``) → garder si
meilleur (ratchet : le score ne régresse JAMAIS). L'export Specctra DSN et le
pont freerouting documentent la chaîne d'évaluation complète.
"""
