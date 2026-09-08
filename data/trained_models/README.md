# data/trained_models/ — registre des modèles (cerveau IA)

Ce domaine isole les **artefacts de modèles** produits par l'entraînement et
consommés par `ai_engine` : world_model, policy_network (rl_agent), embeddings
RAG (Siemens Fuse). Le registre est la colonne vertébrale du rollback : quand
l'alerte `KeeperAcceptanceRateLow` se déclenche, on repointe le service sur la
dernière version saine — sans redéploiement.

## Les trois familles de modèles

| Modèle | Rôle | Entrée → Sortie | Consommateur |
|---|---|---|---|
| `world_model` | prédit le résultat d'une action de placement/routage (montée thermique, densité, conflits) | état de la Board + action → métriques projetées | rl_agent (planification), autonomous_optimizer |
| `policy_network` | propose les actions de placement/routage (RL) | état de la Board → distribution d'actions | rl_agent, keeper_logic |
| `embeddings_rag` | plongement des datasheets/notes d'application pour la recherche sémantique | texte datasheet → vecteur | llm_orchestrator (RAG Siemens Fuse) |

## Versionnage — la règle des trois références

Chaque version enregistrée porte **obligatoirement** :

1. **le jeu d'entraînement** : empreinte (hash SHA-256) des sources —
   `rl_training_set.jsonl` (data/manufacturing_feedback/), corpus de replays
   `tests/vs_quilter_benchmark/corpus/`, snapshots data/projects retenus par le
   keeper — pas de version sans traçabilité de données ;
2. **les métriques de validation** : score DRC moyen, taux d'acceptation
   keeper, vias/design, latence fast_eval p95 — mesurées sur le corpus de
   benchmark, pas sur le train ;
3. **le digest de l'image** qui a produit l'artefact (reproductibilité
   Circuitron, voir .infra/docker_toolchain/versions.lock).

Sans ces trois références, `registry add` refuse l'entrée (le script vérifie ;
les fiches vivent dans `model_registry.md` + manifeste JSON par version dans
`policy_network/v1.3/manifest.json`).

## Layout

```
data/trained_models/
├── README.md                  # ce document — conventions du registre
├── model_registry.md          # fiches modèles (une section par version)
├── world_model/v*/            # artefacts + manifest.json (hash data + métriques)
├── policy_network/v*/         # ex. v1.3/checkpoints/, manifest.json
└── embeddings_rag/v*/         # index vectoriel (faiss/numpy) + manifest.json
```

## Rollback (procédure d'incident)

1. L'alerte `KeeperAcceptanceRateLow` (< 30 % sur 1 h) ou un pic
   `ViasPerDesignSpike` se déclenche ;
2. consulter `model_registry.md` : la version précédente avec
   `keeper_acceptance >= 0.55` et `status: certified` ;
3. pointer `ai_engine` dessus (`MODEL_POLICY_VERSION=v1.2` via Helm
   `--set`, sans rebuild) ;
4. re-certifier la version défaillante hors production (replay du corpus
   benchmark 3×), soit la requalifier avec un nouveau jeu d'entraînement,
   soit l'archiver (`status: retired`).

Les artefacts de modèles sont volumineux mais immuables : en production ils
vivent dans le bucket `pcb-projects` (S3/MinIO) sous `models/`, ce dossier
n'indexe que les manifestes légers + les fiches.
