# pcb_ai_designer_v2 — plateforme de conception PCB pilotée par IA

> **Essaim d'agents — RL + LLM + symbolique** couvrant l'ensemble des angles
> morts de Quilter. Implémentation de référence de la spécification technique
> `specification_pcb_ai_designer_v2.pdf`.

## Vue d'ensemble

Ce dépôt implémente intégralement l'arborescence de la spécification :

| Couche racine | Rôle | Sections spec |
|---|---|---|
| `.infra/` | Kubernetes (Helm), Prometheus/Grafana, outillage Docker reproductible [Circuitron] | 07 |
| `frontend/` | Interface React / Three.js — WebGL 60 fps, chat, éditeur chirurgical | 03 |
| `backend/` | api_gateway (FastAPI/GraphQL/MCP), orchestrator multi-agents, 7 services gRPC | 04–06 |
| `data/` | Projets S3, bibliothèque composants, feedback usine, modèles, graphe Neo4j | 07 |
| `tests/` | Harnais de benchmark `vs_quilter_benchmark` | 07 |
| `proto/` | Contrats gRPC versionnés v1 (source de vérité des interfaces) | 11 |
| `common/` | Socle partagé : modèle de design, événements typés, bus de contraintes, crédits | 11 |

## Les six briques technologiques intégrées

- **DeepPCB** — routage en direct (WebSocket), réduction de vias −44 %, crédits pay-as-you-go
- **Siemens Fuse** — auto-vérification physique (self_verifier), multi-agents, RAG datasheets
- **Cadence AuraStack** — modèle mental partagé (intent_graph), super-agent, multiphysique continue
- **Flux.ai** — chat conversationnel, MCP server, modifications chirurgicales, firmware HW/SW
- **Circuitron** — NL→SKiDL, pipeline d'agents, graphe Neo4j, conteneurisation reproductible
- **AutoPCB** — boucle ratchet nocturne (~300 itérations ≈ 1,20 USD, gain ×5,8)

## Démarrage rapide

```bash
# 1. Environnement Python (≥ 3.11) — un seul venv pour tous les services en dev
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Générer les stubs gRPC depuis proto/
make proto

# 3. Lancer la plateforme complète (Redis, Neo4j, MinIO, Postgres, Ollama, Grafana...)
cp .env.example .env
docker compose up -d redis neo4j minio postgres ollama
docker compose exec ollama ollama pull qwen2.5-coder:14b   # LLM local du RAG (~9 Go)
# GPU NVIDIA ? docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d ollama

# 4. Démarrer les services (terminaux séparés ou make start)
make start-services      # gateway :8000 + les 7 services gRPC

# 5. Frontend
cd frontend && npm install && npm run dev   # http://localhost:5173

# 6. Benchmark vs Quilter
make benchmark           # tests/vs_quilter_benchmark — score, vias, convergence
```

## LLM local (Ollama) branché dans le RAG

Le retriever RAG peut déléguer la rédaction des réponses à un **vrai LLM local**
servi par Ollama (API native `/api/chat`, `/api/tags`, `/api/embeddings`), sans
jamais dépendre du réseau : tout échec retombe instantanément sur le mode
extractif déterministe (réponses citées `[source p.X]`).

```bash
LLM_PROVIDER=ollama LLM_MODEL=qwen2.5-coder:14b \   # variables : .env.example
OLLAMA_BASE_URL=http://localhost:11434 make start-services
```

Le client natif vit dans `rag_engine/ollama_client.py` ; la doc complète est
sous `docs/sphinx/_build/html/ollama_rag.html` et les tests end-to-end (serveur
simulé, zéro dépendance réseau) sous `tests/unit/test_ollama_rag.py`.
`make ollama-setup` (scripts/ollama_setup.sh) détecte binaire natif ou compose,
tire le modèle et conseille un repli (`llama3.1:8b`) si la VRAM est < 10 Go.

## Passe RL d'entraînement (torch)

La passe offline renforce le cerveau avant déploiement du runtime : collecte de
transitions dans un environnement de placement type gym, pré-entraînement du world
model (miroir exact du TinyNet numpy → export `.npz` chargeable tel quel), puis
policy-gradient **REINFORCE** (baseline EMA + entropie) sur la policy de
placement. torch est optionnel — le runtime numpy continue de fonctionner sans.

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
make train-rl          # artefacts : data/trained_models/rl_checkpoints/
make train-rl-quick    # smoke test ~2 s
```

Artefacts produits : `world_model_torch.npz`, `policy_reinforce.pt`,
`training_report.json` (courbes d'apprentissage, résumé de l'archive keeper).
L'export npz est installé au **slot runtime**
`data/trained_models/world_model_torch.npz` (variable `PCB_WORLD_MODEL_NPZ`).

### Boucle nocturne branchée au world model

Chaque nuit, `make nightly` enchaîne la passe RL puis la boucle ratchet du
cerveau IA : `run_night_optimization` **charge `world_model_torch.npz` en warm
start** à l'ouverture de la nuit, prédit les conséquences des placements avec
les poids torch, et **réécrit le slot en fin de nuit** avec les poids mis à
jour par les itérations gardées — le modèle de la veille sert de départ à la
suivante. Le bilan de chaque nuit est disponible via `AiEngine.last_night_summary`
(warm start, score initial/final, gain, slot réécrit).

```bash
make nightly          # passe RL complète + boucle ratchet 300 itérations
make nightly-quick    # smoke : passe RL courte + 60 itérations (~5 s)
# nuit du smoke test : score 0,544 → 0,6122 (+12,55 %), 5 keep / 1 reject
```

## Documentation

| Chaîne | Portée | Sortie |
|---|---|---|
| **Sphinx** (autodoc + napoleon) | API Python complète — common, ai_engine, rl_agent, orchestrator, services | `docs/sphinx/_build/html/index.html` |
| **Doxygen** | Python + noyaux de simulation **C++/CUDA** + README en page principale | `docs/doxygen/build/html/index.html` |

```bash
make docs              # les deux chaînes (doxygen requis pour la 2e)
make docs-sphinx       # Sphinx seul : pip install sphinx sphinx-rtd-theme
make docs-doxygen      # Doxygen seul : apt install doxygen
```

Le HTML généré est versionné dans le dépôt : `docs/sphinx/_build/html/` et
`docs/doxygen/build/html/` sont consultables directement depuis GitHub.
La chaîne Sphinx est aussi servie en ligne via GitHub Pages :
**https://assihervey-coder.github.io/yahriacad-revo-V2/**
(source : branche `gh-pages`, mise à jour par `make docs-pages`).

## Ordre d'implémentation recommandé (section 11)

1. **Socle** : `state_manager`, `constraint_bus`, gateway REST minimale
2. **Chaîne déterministe** : `parser`, `drc_dfm_engine`, `exporter`
3. **Cœur IA** : `rl_agent`, `self_verifier`, `llm_orchestrator`, `autonomous_optimizer`
4. **Différenciateurs** : `websocket_live`, `kicad_live_host`, `surgical_editor`, `firmware_bridge`, `mcp_server`

Chaque étape livre une valeur testable et alimente le benchmark.

## Makefile

`make help` liste toutes les cibles : `proto`, `start-services`, `lint`,
`test`, `benchmark`, `train-rl`, `docs`, `docs-sphinx`, `docs-doxygen`,
`ollama-pull`, `compose-up`, `compose-down`.

## Licences & mentions

Références techniques (DeepPCB, Siemens Fuse, Cadence AuraStack, Flux.ai,
Circuitron, AutoPCB) : qualités d'architecture intégrées, sans réutilisation de
code propriétaire. Les noms de produits cités appartiennent à leurs
propriétaires respectifs.
