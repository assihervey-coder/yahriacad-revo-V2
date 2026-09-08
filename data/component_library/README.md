# data/component_library/ — bibliothèque de composants (catalogue technique)

Source de vérité du BOM : MPN, empreintes, dimensions, prix, stocks. Le
planner_agent pioche ici pour transformer une demande en langage naturel en
netliste concrète ; le parser valide les MPN entrants contre ce catalogue.

## Sources & synchronisation

| Source | Usage | Fréquence de sync |
|---|---|---|
| DigiKey API (v4 OAuth2) | prix, stocks, durées d'approvisionnement | 1×/jour (03:00 UTC) |
| Mouser Search API | comparaison prix + stocks (deuxième source) | 1×/jour (03:30 UTC) |
| KiCad 8 libraries (footprints/symbols) | empreintes & symboles — gelées via image pcb-base | à chaque bump d'image de base |

La sync quotidienne (runner CI planifié) met à jour `price_usd`, `stock` et
`alternate_mpns` en PostgreSQL (voir `schema.sql`) ; `seed_components.json`
est le **germe versionné** : il sert d'amorçage pour les environnements hors
ligne (dev, CI, benchmark) et de base de comparaison pour détecter les dérives
de prix.

## Champs du catalogue

| Champ | Type | Description |
|---|---|---|
| `ref_prefix` | str | préfixe de référence PCB (`U`, `R`, `C`…) — génère U1, R12… |
| `mpn` | str | référence fabricant (clé primaire métier) |
| `description` | str | description FR/EN concise |
| `footprint` | str | empreinte KiCad 8 (`Package_QFP:LQFP-48_7x7mm_P0.5mm`…) |
| `pins` | int | nombre de broches |
| `width_mm`, `height_mm` | float | empreinte au sol (mm) — utilisée par le placement RL |
| `power_w` | float | dissipation max (W) — consommée par la simulation thermique |
| `price_usd` | float | prix unitaire par 1k (source DigiKey) |
| `stock` | int | stock total agrégé (DigiKey + Mouser) |
| `functional_block` | str | bloc fonctionnel (`mcu`, `power`, `rf`, `sensor`…) — regroupement placement |
| `keywords` | [str] | mots-clés pour le RAG datasheets (Siemens Fuse) et le LLM planner |
| `alternate_mpns` | [str] | équivalences validées (2nd source) — table `mpn_alternates` |

## Fichiers

- `seed_components.json` — germe de 24 composants réalistes (MCU, RF, power,
  capteurs, connecteurs, passifs 0402/0603, cristaux, LDO…)
- `schema.sql` — schéma PostgreSQL (`components`, `mpn_alternates`, index) ;
  appliqué par `docker compose exec postgres psql -U pcb -d pcb_designer -f -`

## Validation

Tout MPN non résolu dans le catalogue est signalé `bom_validated=false` par le
parser (événement `erc_error` si critique) : on ne route jamais un design avec
un composant fantôme — leçon des mauvaises surprises de sourcing.
