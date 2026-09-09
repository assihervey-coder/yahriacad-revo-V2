"""Boucle multiphysique continue [Cadence AuraStack] — section 6.2.

Ré-analyse thermal + SI + EM à chaque commit du state_manager (hook callback)
ou périodiquement via un thread léger, puis publie les contraintes révisées
(THERMAL_ZONE_UPDATE, IMPEDANCE_TARGET) sur le constraint_bus et expose les
zones à re-optimiser pour le rl_agent.
"""
