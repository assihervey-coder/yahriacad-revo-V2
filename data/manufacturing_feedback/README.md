# data/manufacturing_feedback/ — boucle usine (Siemens Fuse, étape 8 du workflow)

Ce domaine ferme la boucle **usine → plateforme** : après fabrication réelle,
les rendements et défauts AOI (Automated Optical Inspection) remontent pour
(1) requalifier les règles DRC/DFM du `drc_dfm_engine` et (2) produire des
signaux d'entraînement supervisés pour le placement RL.

## La boucle Siemens Fuse

```
usine (fab) ──► factory_runs.csv + defects_aoi.csv ──► ingest.py ──► PostgreSQL (ou sqlite dev)
                                                                      │
                                    ┌─────────────────────────────────┤
                                    ▼                                 ▼
                    rule_requalification                     rl_training_set.jsonl
              (règles DRC/DFM à resserrer/assouplir)        (placement_features → penalty)
                                    │                                 │
                                    ▼                                 ▼
                        drc_dfm_engine (règles v n+1)          rl_agent (fine-tuning)
```

1. **Rendements** : `factory_runs` agrège par run de fabrication (panneaux,
   unités produites, unités échouées) → taux de rendement par projet/design.
2. **Défauts AOI** : `defects` classe chaque défaut (tombstone, solder_bridge,
   component_shift, insufficient_solder…) avec sa position (x, y en mm) et le
   MPN concerné → le taux de défauts **par motif de placement** identifie les
   décisions du placement RL qui coûtent du rendement.
3. **Requalification** : quand un motif dépasse le seuil (par défaut 0,5 % de
   défauts sur 3 runs), une ligne `rule_requalification` est créée : la règle
   DRC/DFM correspondante est marquée « à resserrer » (ou « à assouplir » si
   les défauts sont trop faibles et la contrainte inutilement coûteuse). Le
   `drc_dfm_engine` rejoue ses règles versionnées depuis ce signal.
4. **Signaux RL** : `ingest.py` exporte `rl_training_set.jsonl` — un couple
   (placement_features → penalty) par instance de composant observée ; ces
   échantillons nourrissent le fine-tuning du `rl_agent` (world_model +
   policy_network, voir data/trained_models).

## Fichiers

| Fichier | Rôle |
|---|---|
| `schema.sql` | schéma PostgreSQL (`factory_runs`, `defects`, `rule_requalification`) |
| `ingest.py` | pipeline CSV → base, calcul des taux par motif, export RL (CLI) |

## Utilisation d'ingest.py

```bash
# Dev / CI (sqlite, aucun serveur requis) :
python3 data/manufacturing_feedback/ingest.py \
    --factory-csv data/manufacturing_feedback/sample_factory_runs.csv \
    --defects-csv data/manufacturing_feedback/sample_defects_aoi.csv \
    --db data/manufacturing_feedback/feedback.sqlite3 \
    --export-rl data/manufacturing_feedback/rl_training_set.jsonl

# Production (PostgreSQL du docker-compose) :
python3 data/manufacturing_feedback/ingest.py \
    --factory-csv runs.csv --defects-csv defects.csv \
    --pg-dsn postgresql://pcb:pcb-dev@localhost:5432/pcb_designer
```

Colonnes attendues :

- `factory_runs.csv` : `run_id,project_id,design_version,fab,fab_date,panels,units_produced,units_failed,process`
- `defects_aoi.csv` : `defect_id,run_id,ref,mpn,defect_type,x_mm,y_mm,placement_pattern,disposition`

`placement_pattern` décrit le contexte de placement du composant (ex.
`edge_connector`, `bga_center`, `dense_group`, `near_board_edge`) — c'est la
clé d'agrégation des taux de défauts ; en production il est déduit du
`design.json` par le state_manager, en dev il vient du CSV AOI.
