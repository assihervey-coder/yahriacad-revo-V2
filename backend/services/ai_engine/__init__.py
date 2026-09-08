"""Le cerveau IA — cinq sous-systèmes communicant par le constraint_bus (section 05).

Sous-systèmes : shared_mental_model (graphe d'intention + bus de contraintes),
llm_orchestrator (RAG datasheets + graphe Neo4j + optimisation de prompts),
rl_agent (world model DreamerV3 + policy + espace d'action), self_verifier
(vérification déterministe + rollback) et autonomous_optimizer (boucle ratchet
nocturne). Chaque sous-système fonctionne hors ligne : tout appel à un LLM,
à torch ou à Neo4j passe par un adaptateur à fallback déterministe numpy.
"""

from __future__ import annotations

__all__ = [
    "shared_mental_model",
    "llm_orchestrator",
    "rl_agent",
    "self_verifier",
    "autonomous_optimizer",
]
__version__ = "1.0.0"
