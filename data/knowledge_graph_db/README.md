# data/knowledge_graph_db/ — graphe de connaissances [Circuitron]

Base **Neo4j 5** (docker-compose.yml, service `neo4j` — `NEO4J_URI` dans
l'environnement). C'est la **mémoire technique long terme** de la plateforme :
là où data/projects versionne les designs, le graphe accumule le *savoir*
dérivé — motifs de circuits validés, règles DRC/DFM dérivées des retours
usine, associations composant ↔ bloc fonctionnel ↔ pièges de conception.

## Rôle mémoire technique

Le `llm_orchestrator` interroge le graphe (Cypher) à chaque planification
multi-agents pour ancrer ses décisions dans l'expérience accumulée :

- **motifs validés** : chaque motif dont les designs sont passés en production
  avec un bon rendement usine porte un score de confiance — le planner réutilise
  ces motifs plutôt que de réinventer (c'est la promesse Circuitron : le
  système *apprend* de chaque projet) ;
- **contraintes héritées** : un MPN qui a déjà généré des défauts AOI porte ses
  recommandations de placement (clearance, keepout, découplage renforcé) ;
- **alternative 2nd source** : relations `:ALTERNATE` issues de
  data/component_library (mpn_alternates) — consultables sans interroger
  DigiKey.

## Motifs accumulés

Les 5 motifs seed (`init.cypher`) couvrent les cas d'école ratés par les
router historiques : régulateur LDO avec découplage, pull-up I2C, résistances
CC de l'USB-C, découplage STM32, keepout antenne LoRa. Chaque nouveau design
certifié par le keeper enrichit le graphe (`:VALIDATED_ON` vers le projet) —
le score de confiance monte, les motifs marginaux sont élagués par la tâche
hebdomadaire `prune_low_confidence_patterns`.

## Initialisation

```bash
docker compose up -d neo4j
# Contraintes + index + motifs seed :
docker compose exec -T neo4j cypher-shell -u neo4j -p pcb-dev \
    -f /data/init.cypher        # ou : cat data/knowledge_graph_db/init.cypher | cypher-shell ...
```

Le script est **idempotent** (MERGE partout) : rejouable à chaque démarrage.

## Requêtes d'usage

```cypher
// Motifs applicables à un MPN donné (planner) :
MATCH (c:Component {mpn: 'STM32F411CEU6'})-[:PART_OF]->(p:DesignPattern)
RETURN p.name, p.confidence ORDER BY p.confidence DESC;

// Règles DRC dérivées des défauts usine (manufacturing_feedback) :
MATCH (r:DerivedRule {source: 'manufacturing_feedback'})
WHERE r.last_requalified > date() - duration('P30D')
RETURN r.rule_id, r.action, r.defect_rate;
```

## Modèle de données (seed)

```
(:Component {mpn, ref_prefix, functional_block})
(:DesignPattern {name, category, confidence, validated_runs})
(:Rule {rule_id, source, kind, min_clearance_mm, last_requalified})
(:Component)-[:PART_OF]->(:DesignPattern)
(:DesignPattern)-[:REQUIRES]->(:Rule)
(:Component)-[:ALTERNATE {verified}]->(:Component)
```
