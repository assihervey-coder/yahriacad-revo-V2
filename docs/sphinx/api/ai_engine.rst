Cerveau IA (``ai_engine``)
==========================

Les cinq sous-systèmes du cerveau : modèle mental partagé, orchestrateur LLM
(RAG + Ollama), agent RL, auto-vérification et optimiseur autonome.

Modèle mental partagé
---------------------

.. automodule:: backend.services.ai_engine.shared_mental_model.intent_graph.graph

.. automodule:: backend.services.ai_engine.shared_mental_model.constraint_bus.service

RAG datasheets
--------------

.. automodule:: backend.services.ai_engine.llm_orchestrator.rag_engine.chunker

.. automodule:: backend.services.ai_engine.llm_orchestrator.rag_engine.indexer

.. automodule:: backend.services.ai_engine.llm_orchestrator.rag_engine.retriever

.. automodule:: backend.services.ai_engine.llm_orchestrator.rag_engine.ollama_client

Graphe de connaissances & prompts
---------------------------------

.. automodule:: backend.services.ai_engine.llm_orchestrator.knowledge_graph.neo4j_client

.. automodule:: backend.services.ai_engine.llm_orchestrator.knowledge_graph.pattern_validator

.. automodule:: backend.services.ai_engine.llm_orchestrator.prompt_optimizer.optimizer

Auto-vérification
-----------------

.. automodule:: backend.services.ai_engine.self_verifier.deterministic_checker.checker

.. automodule:: backend.services.ai_engine.self_verifier.rollback_manager.rollback

Optimiseur autonome
-------------------

.. automodule:: backend.services.ai_engine.autonomous_optimizer.proposer_llm.proposer

.. automodule:: backend.services.ai_engine.autonomous_optimizer.fast_evaluator.freerouting_bridge

.. automodule:: backend.services.ai_engine.autonomous_optimizer.keeper_logic.keeper

Point d'entrée du service
-------------------------

.. automodule:: backend.services.ai_engine.main
