# Registre des modèles — fiches de version (model cards)

Une fiche par version publiée. Une version ne passe `certified` qu'après :
métriques de validation mesurées sur le corpus benchmark, trois références
complètes (jeu d'entraînement, métriques, digest d'image) et signature revue
d'architecture. Format de fiche = gabarit ci-dessous, instance réelle pour
`policy_network v1.3`.

---

## policy_network — v1.3 (certifiée, production)

| Champ | Valeur |
|---|---|
| Famille | policy_network (rl_agent — placement & routage) |
| Architecture | GNN (message passing 4 couches) + tête d'action duale (placement MLP, routage MLP) |
| Paramètres | 18,4 M |
| Framework / runtime | PyTorch 2.3 (entraînement) → TorchScript (inférence CPU+GPU, image pcb-base-cuda12.4) |
| Date de certification | 2024-06-12 |
| Statut | **certified — en production** (ai_engine, Helm `MODEL_POLICY_VERSION=v1.3`) |
| Remplace | v1.2 (certified → archived le 2024-06-12) |

### Référence 1 — jeu d'entraînement

| Source | Empreinte SHA-256 | Volume |
|---|---|---|
| `data/manufacturing_feedback/rl_training_set.jsonl` (pénalités usine v4) | `9f2c4e…` | 48 200 échantillons |
| Snapshots data/projects retenus par le keeper (nov. 2023 → mai 2024) | `b71d08…` | 12 640 designs |
| Corpus benchmark `tests/vs_quilter_benchmark/corpus/` (exclu du train, réservé validation) | — | 3 designs de référence |

### Référence 2 — métriques de validation (corpus benchmark, médiane sur 3 replays)

| Métrique | v1.2 (précédente) | **v1.3** | Seuil de certification |
|---|---|---|---|
| score DRC moyen | 94,1 | **96,3** | ≥ 95 |
| taux d'acceptation keeper | 0,58 | **0,67** | ≥ 0,55 |
| vias par design (moyenne) | 41,7 | **33,4** (−20 %) | ≤ 38 |
| latence fast_eval p95 | 1,8 s | **1,6 s** | ≤ 5 s |
| convergence complète (3 designs) | 47,2 s | **41,9 s** | — |

### Référence 3 — reproductibilité

| Artefact | Empreinte |
|---|---|
| Image d'entraînement | `registry.example.com/pcb/pcb-base@sha256:8c11a0…` (versions.lock 2024-06-12) |
| Graine RNG (train) | 20240612 |
| manifest.json | `policy_network/v1.3/manifest.json` (hash des 2 jeux + métriques + graine) |

### Signaux d'entraînement consommés

- pénalités usine par motif de placement (`ingest.py`, requalification du
  2024-06-03 : `dfm.tombstone` tighten sur motif `dense_group`) ;
- rejouer des transitions validées par le keeper (ratchet nocturne AutoPCB,
  ~300 itérations/nuit × 90 nuits).

### Limites connues

- corpus de validation limité à 3 designs de référence (le gate CI couvre la
  régression mais pas la généralisation) — élargissement en cours (objectif
  15 designs) ;
- non entraînée sur designs flexibles (rigid only) ;
- latence de convergence dégradée au-delà de 500 nets (utiliser le mode
  `fast_eval` pour le pré-épandage).

---

## Gabarit de fiche (copier pour chaque nouvelle version)

```markdown
## <famille> — vX.Y (draft | certified | archived | retired)

| Champ | Valeur |
|---|---|
| Famille | world_model | policy_network | embeddings_rag |
| Architecture | … |
| Paramètres | … |
| Framework / runtime | … |
| Date de certification | AAAA-MM-JJ |
| Statut | draft → certified → archived/retired |
| Remplace | vX.Y-1 |

### Référence 1 — jeu d'entraînement
| Source | Empreinte SHA-256 | Volume |

### Référence 2 — métriques de validation
| Métrique | précédente | actuelle | Seuil |

### Référence 3 — reproductibilité
| Artefact | Empreinte |

### Signaux d'entraînement consommés
### Limites connues
```
