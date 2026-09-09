# data/projects/ — persistance des projets (S3 compatible, MinIO en dev)

Ce domaine isole **la persistance des designs** : chaque projet utilisateur,
ses versions immuables et ses exports. Le bucket est `pcb-projects`
(`S3_BUCKET`, voir docker-compose.yml → service `minio`).

## Layout S3

```
projects/{project_id}/
├── journal.jsonl                    # journal d'audit global (append-only)
└── versions/
    ├── 1/
    │   ├── design.json              # DesignState sérialisé (Board + placements + nets)
    │   ├── journal.jsonl            # transitions de CETTE version (state_manager)
    │   └── exports/
    │       ├── gerber/…             # sorties de l'exporter (étape 8 du workflow)
    │       ├── odb++.tgz
    │       └── bom.csv
    ├── 2/
    │   └── …
    └── n/ …
```

Conventions :

- `design.json` = snapshot complet d'un `DesignState` (aggregat du
  state_manager) — assez pour rejouer la version sans son historique ;
- `journal.jsonl` = une ligne JSON par transition (auteur, note, drc_score,
  vias…) — c'est ce que lit le `rollback_manager` (self_verifier) et rejoue le
  `session_restorer` ;
- les gros binaires (STL viewer_3d, exports) restent sous `exports/` pour
  permettre la reprise de session sans retélécharger l'historique.

## Immuabilité par version

- **une version n'est JAMAIS modifiée** : toute évolution crée `versions/n+1`
  (le ratchet nocturne AutoPCB pousse une nouvelle version par nuit d'opti
  acceptée par le keeper) ;
- écriture via PUT-IF-NOT-EXISTS (policy MinIO/S3) : les collisions de version
  sont refusées côté stockage, pas seulement côté API ;
- suppression désactivée sur `versions/*` (object-lock mode gouvernance,
  rétention 90 j) ; seul `retention.py` (tâche planifiée) archive vers
  `archive/` les projets inactifs > 180 j avant purge.

## Rétention

| Objet | Rétention | Commentaire |
|---|---|---|
| versions/* design.json + journal | 2 ans | base de reprise & audit |
| versions/*/exports/ | 180 j | régénérable depuis design.json |
| projets inactifs (0 session 180 j) | archivés puis purgés | comptabilité crédits conservée (credits.json → PostgreSQL) |

Le grand livre de crédits (`credits.json`) vit ici au niveau projet en
développement ; en production il est adossé à PostgreSQL (voir
`common/credits.py`).
