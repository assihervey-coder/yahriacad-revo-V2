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

# 3. Lancer la plateforme complète (Redis, Neo4j, MinIO, Postgres, Grafana...)
cp .env.example .env
docker compose up -d redis neo4j minio postgres

# 4. Démarrer les services (terminaux séparés ou make start)
make start-services      # gateway :8000 + les 7 services gRPC

# 5. Frontend
cd frontend && npm install && npm run dev   # http://localhost:5173

# 6. Benchmark vs Quilter
make benchmark           # tests/vs_quilter_benchmark — score, vias, convergence
```

## Ordre d'implémentation recommandé (section 11)

1. **Socle** : `state_manager`, `constraint_bus`, gateway REST minimale
2. **Chaîne déterministe** : `parser`, `drc_dfm_engine`, `exporter`
3. **Cœur IA** : `rl_agent`, `self_verifier`, `llm_orchestrator`, `autonomous_optimizer`
4. **Différenciateurs** : `websocket_live`, `kicad_live_host`, `surgical_editor`, `firmware_bridge`, `mcp_server`

Chaque étape livre une valeur testable et alimente le benchmark.

## Makefile

`make help` liste toutes les cibles : `proto`, `start-services`, `lint`,
`test`, `benchmark`, `compose-up`, `compose-down`.

## Licences & mentions

Références techniques (DeepPCB, Siemens Fuse, Cadence AuraStack, Flux.ai,
Circuitron, AutoPCB) : qualités d'architecture intégrées, sans réutilisation de
code propriétaire. Les noms de produits cités appartiennent à leurs
propriétaires respectifs.
