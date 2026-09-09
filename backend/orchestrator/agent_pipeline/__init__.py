"""Sous-paquet agent_pipeline — les 6 agents spécialisés [pipeline Circuitron].

planner → researcher → selector → code_generator → validator → corrector.
Chaque agent hérite de `base_agent.BaseAgent` (journalisation systématique,
escalade vers le super_agent) et reste exécutable hors ligne (fallbacks).
"""
