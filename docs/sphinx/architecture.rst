Architecture
============

Le dépôt est organisé en cinq couches racine dont la séparation reflète les
frontières de déploiement :

.. list-table::
   :header-rows: 1
   :widths: 18 44 38

   * - Couche
     - Rôle
     - Contenu clé
   * - ``frontend/``
     - Interface React / Three.js (WebGL 60 fps), chat, éditeur chirurgical
     - ``chat_interface``, ``viewer_3d``, ``surgical_editor``, ``pipeline_bar``
   * - ``backend/``
     - api_gateway (FastAPI/GraphQL/MCP), orchestrator multi-agents, 7 services gRPC
     - ``ai_engine``, ``parser``, ``simulator``, ``router``, ``drc_dfm_engine``,
       ``exporter``, ``firmware_bridge``, ``pcb_plugin``
   * - ``common/``
     - Socle partagé : modèle de design, événements typés, bus de contraintes
     - :mod:`common.design_model`, :mod:`common.events`, :mod:`common.bus`,
       :mod:`common.credits`
   * - ``data/``
     - Projets S3, bibliothèque de composants, feedback usine, modèles entraînés
     - ``trained_models/rl_checkpoints/``, ``vector_store/``
   * - ``proto/``
     - Contrats gRPC versionnés v1 — source de vérité des interfaces
     - 9 fichiers ``.proto`` (orchestrator, ai_engine, simulator, router…)

Le cerveau IA
-------------

:mod:`backend.services.ai_engine` agrège cinq sous-systèmes communicant par le
**bus de contraintes** (latence cible < 50 ms) :

1. **shared_mental_model** — :mod:`~backend.services.ai_engine.shared_mental_model.intent_graph.graph`
   (graphe d'intention versionné) et
   :mod:`~backend.services.ai_engine.shared_mental_model.constraint_bus.service`
   (diffusion zones/impédances/longueurs).
2. **llm_orchestrator** — RAG datasheets (``chunker`` → ``indexer`` → ``retriever``
   + :mod:`~backend.services.ai_engine.llm_orchestrator.rag_engine.ollama_client`
   pour le LLM local), graphe de connaissances Neo4j, validateur de motifs SKiDL,
   optimiseur de prompts.
3. **rl_agent** — espace d'action, world model « DreamerV3-like »,
   policy ε-greedy, et la **passe d'entraînement torch** (voir :doc:`rl_training`).
4. **self_verifier** — vérificateur déterministe (géométrie, électrique,
   routabilité A*) et gestionnaire de rollback.
5. **autonomous_optimizer** — proposer d'heuristiques entrelacées, évaluateur
   rapide (bridge freerouting), règle du ratchet (keeper).

Workflow 8 étapes
-----------------

La chaîne déterministe parser → planner → placement/routage → DRC → export est
orchestrée par :mod:`backend.orchestrator` : chaque étape est portée par un
agent dédié (``agent_pipeline/``), supervisée par le super-agent
(``super_agent/`` : allocation de ressources + arbitrage de conflits) et
journalisée par le :mod:`~backend.orchestrator.state_manager.manager` (deltas,
verrous, rollback).
