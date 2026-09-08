Orchestrator multi-agents
=========================

Le pipeline des 8 étapes, le super-agent, la gestion d'état versionnée et le
journal des transitions.

Point d'entrée
--------------

.. automodule:: backend.orchestrator.main

.. automodule:: backend.orchestrator.adapters

Super-agent
-----------

.. automodule:: backend.orchestrator.super_agent.super_agent

.. automodule:: backend.orchestrator.super_agent.plan

.. automodule:: backend.orchestrator.super_agent.resource_allocator

.. automodule:: backend.orchestrator.super_agent.conflict_arbiter

Pipeline d'agents
-----------------

.. automodule:: backend.orchestrator.agent_pipeline.base_agent

.. automodule:: backend.orchestrator.agent_pipeline.planner_agent.agent

.. automodule:: backend.orchestrator.agent_pipeline.researcher_agent.agent

.. automodule:: backend.orchestrator.agent_pipeline.selector_agent.agent

.. automodule:: backend.orchestrator.agent_pipeline.code_generator.agent

.. automodule:: backend.orchestrator.agent_pipeline.validator_agent.agent

.. automodule:: backend.orchestrator.agent_pipeline.corrector_agent.agent

Gestion d'état
--------------

.. automodule:: backend.orchestrator.state_manager.manager

.. automodule:: backend.orchestrator.state_manager.journal

.. automodule:: backend.orchestrator.state_manager.deltas

.. automodule:: backend.orchestrator.state_manager.locks
