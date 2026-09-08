Usage — cibles make et services
===============================

Démarrage rapide
----------------

.. code-block:: bash

   make test            # tests unitaires (pytest, aucun réseau requis)
   make proto           # régénère les stubs gRPC vers backend/proto_gen/
   make start-services  # gateway + 7 services gRPC en arrière-plan
   make benchmark       # harnais vs Quilter (gate CI)

Pile docker compose
-------------------

.. code-block:: bash

   docker compose up -d          # redis, neo4j, minio, postgres, ollama, monitoring…
   make compose-down             # arrêt + suppression des volumes

Le service ``ollama`` expose ``11434`` ; ``ai_engine`` est préconfiguré avec
``LLM_PROVIDER=ollama`` et ``OLLAMA_BASE_URL=http://ollama:11434`` (voir
:doc:`ollama_rag`).

Entraînement RL
---------------

.. code-block:: bash

   make train-rl         # passe complète : dataset, world model torch, REINFORCE
   make train-rl-quick   # passe courte (jeux de paramètres réduits)

Documentation
-------------

.. code-block:: bash

   make docs             # Sphinx + Doxygen (si binaire disponible)
   make docs-sphinx      # API Python -> docs/sphinx/_build/html/index.html
   make docs-doxygen     # Python + noyaux C++/CUDA -> docs/doxygen/build/html/index.html

Lint et propreté
----------------

.. code-block:: bash

   make lint             # compileall (+ ruff si installé)
   make clean            # caches Python, stubs gRPC, artefacts out/
