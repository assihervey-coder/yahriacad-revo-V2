LLM local (Ollama) branché dans le RAG
======================================

Le retriever RAG s'appuie par défaut sur le mode *extractif* déterministe
(les meilleures phrases des passages retrouvés, chacune citée). Depuis la v2,
il peut déléguer la rédaction à un **vrai LLM local servi par Ollama**, sans
jamais dépendre du réseau : tout échec retombe sur le mode extractif.

Installation du serveur
-----------------------

.. code-block:: bash

   # Option A — binaire natif (https://ollama.com)
   ollama pull qwen2.5-coder:14b   # ~9 Go q4_K_M ; repli léger : llama3.1:8b

   # Option B — docker compose (service déjà défini dans le dépôt)
   docker compose up -d ollama
   docker compose exec ollama ollama pull qwen2.5-coder:14b

   # Option C — script tout-en-un (détecte natif/compose, vérifie la VRAM)
   make ollama-setup

Accélération GPU
----------------

Sur une machine NVIDIA (12 Go de VRAM recommandés pour un 14B), active le
serveur GPU avec l'override ``docker-compose.gpu.yml`` (NVIDIA Container Toolkit
requis) :

.. code-block:: bash

   docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d ollama
   docker compose exec ollama ollama pull qwen2.5-coder:14b

Sans GPU, le même modèle tourne en CPU (inférence plus lente) ; le retriever
reste identique et retombe de toute façon sur le mode extractif en cas d'échec.

Configuration
-------------

Variables d'environnement (voir ``.env.example``) :

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Variable
     - Effet
   * - ``LLM_PROVIDER=ollama``
     - Active le client natif Ollama dans le retriever.
   * - ``LLM_MODEL``
     - Modèle servi (défaut ``qwen2.5-coder:14b``, aligné sur ``.env.example``).
   * - ``OLLAMA_BASE_URL``
     - URL du serveur (``http://localhost:11434``, ``http://ollama:11434`` en compose).
   * - ``OLLAMA_TIMEOUT_S`` / ``OLLAMA_NUM_CTX``
     - Timeout de génération (8 s par défaut) et fenêtre de contexte (4096).

Fonctionnement
--------------

1. Le :class:`~backend.services.ai_engine.llm_orchestrator.rag_engine.indexer.TfidfIndex`
   retrouve les passages pertinents des datasheets.
2. Le client :class:`~backend.services.ai_engine.llm_orchestrator.rag_engine.ollama_client.OllamaClient`
   interroge l'API **native** (``/api/tags`` pour la santé, ``/api/chat`` pour la
   génération, ``/api/embeddings`` pour les vecteurs d'appoint).
3. Le prompt système impose l'ancrage : chaque affirmation doit citer sa source
   ``[source p.X]`` — le LLM local n'a jamais le droit de répondre hors contexte.
4. Si Ollama est muet (timeout, serveur arrêté), la réponse extractive est servie
   instantanément et le champ ``mode`` de la réponse vaut ``"extractif"``.

Vérifier le branchement
-----------------------

.. code-block:: bash

   python3 - <<'PY'
   from common.config import Settings
   from backend.services.ai_engine.llm_orchestrator.rag_engine.retriever import RagRetriever

   retriever = RagRetriever(settings=Settings(llm_provider="ollama",
                                              llm_model="qwen2.5-coder:14b"))
   print(retriever.answer("Quelle impédance cible pour l'USB2 ?"))
   # {"answer": "... [ds_usb p.12]", "citations": [...], "mode": "ollama"}
   PY

Les tests unitaires ``tests/unit/test_ollama_rag.py`` valident la chaîne de bout
en bout contre un serveur Ollama simulé (aucun serveur réel requis en CI).
