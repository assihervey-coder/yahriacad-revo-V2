// ===========================================================================
// data/knowledge_graph_db/init.cypher — initialisation Neo4j 5 [Circuitron]
// Contraintes + index + 5 motifs de circuits validés (seed de la mémoire
// technique). Idempotent : MERGE partout, rejouable à chaque démarrage.
// Appliquer : cypher-shell -u neo4j -p <pwd> -f data/knowledge_graph_db/init.cypher
// ===========================================================================

// ---------------------------------------------------------------------------
// 1. Contraintes d'unicité (Neo4j 5 — syntaxe REQUIRE)
// ---------------------------------------------------------------------------
CREATE CONSTRAINT component_mpn IF NOT EXISTS
  FOR (c:Component) REQUIRE c.mpn IS UNIQUE;

CREATE CONSTRAINT design_pattern_name IF NOT EXISTS
  FOR (p:DesignPattern) REQUIRE p.name IS UNIQUE;

CREATE CONSTRAINT rule_rule_id IF NOT EXISTS
  FOR (r:Rule) REQUIRE r.rule_id IS UNIQUE;

// ---------------------------------------------------------------------------
// 2. Index de recherche (planner : filtrer par bloc, trier par confiance)
// ---------------------------------------------------------------------------
CREATE INDEX component_functional_block IF NOT EXISTS
  FOR (c:Component) ON (c.functional_block);

CREATE INDEX design_pattern_confidence IF NOT EXISTS
  FOR (p:DesignPattern) ON (p.confidence);

CREATE INDEX rule_kind IF NOT EXISTS
  FOR (r:Rule) ON (r.kind);

// ---------------------------------------------------------------------------
// 3. Composants seed (miroir de data/component_library/seed_components.json)
// ---------------------------------------------------------------------------
MERGE (c:Component {mpn: 'STM32F411CEU6'})
  SET c.ref_prefix = 'U',
      c.functional_block = 'mcu',
      c.description = 'MCU ARM Cortex-M4F 100 MHz, LQFP-48';

MERGE (c:Component {mpn: 'SX1276IMLTRT'})
  SET c.ref_prefix = 'U',
      c.functional_block = 'rf',
      c.description = 'Emetteur-recepteur LoRa 868/915 MHz, QFN-28';

MERGE (c:Component {mpn: 'AMS1117-3.3'})
  SET c.ref_prefix = 'U',
      c.functional_block = 'power',
      c.description = 'LDO 3,3 V / 1 A, SOT-223';

MERGE (c:Component {mpn: 'MCP1700-3302E/TT'})
  SET c.ref_prefix = 'U',
      c.functional_block = 'power',
      c.description = 'LDO 250 mA faible courant de repos, SOT-23';

MERGE (c:Component {mpn: 'USB4105-GF-A'})
  SET c.ref_prefix = 'J',
      c.functional_block = 'connector',
      c.description = 'Receptacle USB Type-C 2.0, SMD';

MERGE (c:Component {mpn: 'RC0402FR-0710KL'})
  SET c.ref_prefix = 'R',
      c.functional_block = 'passive',
      c.description = 'Resistance 10 kOhm 1 % 0402 (pull-up / CC)';

MERGE (c:Component {mpn: 'CL05B104KO5NNNC'})
  SET c.ref_prefix = 'C',
      c.functional_block = 'passive',
      c.description = 'Condensateur 100 nF X7R 25 V 0402 (decouplage)';

MERGE (c:Component {mpn: '748891026'})
  SET c.ref_prefix = 'ANT',
      c.functional_block = 'rf',
      c.description = 'Antenne SMD 868 MHz ISM/LoRa, 50 Ohm';

// ---------------------------------------------------------------------------
// 4. Règles dérivées (DRC/DFM) portées par les motifs
// ---------------------------------------------------------------------------
MERGE (r:Rule {rule_id: 'drc.input_cap_proximity'})
  SET r.kind = 'placement',
      r.source = 'design_review',
      r.description = 'Condensateur d entree du regulateur a moins de 2 mm de la broche VIN',
      r.max_distance_mm = 2.0;

MERGE (r:Rule {rule_id: 'drc.output_cap_proximity'})
  SET r.kind = 'placement',
      r.source = 'design_review',
      r.description = 'Condensateur de sortie du regulateur a moins de 3 mm de VOUT',
      r.max_distance_mm = 3.0;

MERGE (r:Rule {rule_id: 'drc.i2c_pullup_location'})
  SET r.kind = 'placement',
      r.source = 'design_review',
      r.description = 'Pull-up I2C a moins de 5 mm du bus le plus long, une seule paire par bus',
      r.max_distance_mm = 5.0;

MERGE (r:Rule {rule_id: 'usb.cc_resistor_value_5k1'})
  SET r.kind = 'electrical',
      r.source = 'usb_if_spec',
      r.description = 'Resistances RD des broches CC1/CC2 = 5,1 kOhm +/- 1 % (USB-C sink)',
      r.nominal_value_ohm = 5100.0;

MERGE (r:Rule {rule_id: 'drc.mcu_decoupling_per_vdd'})
  SET r.kind = 'placement',
      r.source = 'design_review',
      r.description = 'Un condensateur 100 nF par broche VDD/VSS, a moins de 1,5 mm de la paire',
      r.max_distance_mm = 1.5;

MERGE (r:Rule {rule_id: 'drc.antenna_keepout_no_copper'})
  SET r.kind = 'keepout',
      r.source = 'rf_review',
      r.description = 'Aucune piste ni plan de cuivre dans le keepout antenne (zone de rayonnement 50 Ohm)',
      r.keepout_margin_mm = 8.0;

// ---------------------------------------------------------------------------
// 5. Motifs de circuits validés (seed — enrichis par chaque design certifié)
// ---------------------------------------------------------------------------

// --- Motif 1 : régulateur LDO avec découplage entrée/sortie -----------------
MERGE (p:DesignPattern {name: 'ldo_regulator_decoupled'})
  SET p.category = 'power',
      p.description = 'LDO 3,3 V avec condensateurs 10 uF en entree et 22 uF en sortie, plus 100 nF de haute frequence a moins de 2 mm de VIN — evite l oscillation des LDO a faible dropout',
      p.confidence = 0.95,
      p.validated_runs = 12;
MERGE (a:Component {mpn: 'AMS1117-3.3'})-[:PART_OF {role: 'regulator'}]->(p);
MERGE (b:Component {mpn: 'MCP1700-3302E/TT'})-[:PART_OF {role: 'regulator_low_iq'}]->(p);
MERGE (r1:Rule {rule_id: 'drc.input_cap_proximity'})-[:GUARD_OF]->(p);
MERGE (r2:Rule {rule_id: 'drc.output_cap_proximity'})-[:GUARD_OF]->(p);

// --- Motif 2 : pull-up I2C (une seule paire par bus) ------------------------
MERGE (p2:DesignPattern {name: 'i2c_pullup_single_pair'})
  SET p2.category = 'bus',
      p2.description = 'Une unique paire de resistances de pull-up (4,7 kOhm typique a 100 kHz, 2,2 kOhm au-dela) sur SDA/SCL, placee a moins de 5 mm du point le plus eloigne du bus — evite les rampes lentes et les doubles pull-up',
      p2.confidence = 0.93,
      p2.validated_runs = 9;
MERGE (rc:Component {mpn: 'RC0402FR-0710KL'})-[:PART_OF {role: 'pullup'}]->(p2);
MERGE (rm:Component {mpn: 'STM32F411CEU6'})-[:PART_OF {role: 'i2c_master'}]->(p2);
MERGE (r3:Rule {rule_id: 'drc.i2c_pullup_location'})-[:GUARD_OF]->(p2);

// --- Motif 3 : USB-C — résistances RD 5,1 kOhm sur CC1/CC2 ------------------
MERGE (p3:DesignPattern {name: 'usb_c_rd_cc_5k1'})
  SET p3.category = 'usb',
      p3.description = 'Equipement USB-C sink : resistances de 5,1 kOhm (1 %) de CC1 et CC2 a la masse, une par broche — sans elles, aucune source n alimente le port ; aucune autre charge sur CC',
      p3.confidence = 0.99,
      p3.validated_runs = 15;
MERGE (usb:Component {mpn: 'USB4105-GF-A'})-[:PART_OF {role: 'receptacle'}]->(p3);
MERGE (cc:Component {mpn: 'RC0402FR-0710KL'})-[:PART_OF {role: 'rd_5k1_note_valeur_regle'}]->(p3);
MERGE (r4:Rule {rule_id: 'usb.cc_resistor_value_5k1'})-[:GUARD_OF]->(p3);

// --- Motif 4 : découplage STM32 — 100 nF par paire VDD ----------------------
MERGE (p4:DesignPattern {name: 'mcu_decoupling_per_vdd'})
  SET p4.category = 'power',
      p4.description = 'Un condensateur 100 nF X7R par paire VDD/VSS du MCU, a moins de 1,5 mm, via court et large vers le plan GND — supprime le bruit de commutation du cortex-M4 a 100 MHz',
      p4.confidence = 0.97,
      p4.validated_runs = 12;
MERGE (mcu:Component {mpn: 'STM32F411CEU6'})-[:PART_OF {role: 'mcu'}]->(p4);
MERGE (dec:Component {mpn: 'CL05B104KO5NNNC'})-[:PART_OF {role: 'decoupling'}]->(p4);
MERGE (r5:Rule {rule_id: 'drc.mcu_decoupling_per_vdd'})-[:GUARD_OF]->(p4);

// --- Motif 5 : antenne LoRa — keepout de rayonnement ------------------------
MERGE (p5:DesignPattern {name: 'lora_antenna_keepout'})
  SET p5.category = 'rf',
      p5.description = 'Antenne SMD 868 MHz en bord de carte, reseau de match 50 Ohm vers SX1276, keepout sans cuivre de 8 mm autour de la zone de rayonnement, plan GND interrompu sous l antenne',
      p5.confidence = 0.92,
      p5.validated_runs = 7;
MERGE (ant:Component {mpn: '748891026'})-[:PART_OF {role: 'antenna'}]->(p5);
MERGE (lora:Component {mpn: 'SX1276IMLTRT'})-[:PART_OF {role: 'transceiver'}]->(p5);
MERGE (r6:Rule {rule_id: 'drc.antenna_keepout_no_copper'})-[:GUARD_OF]->(p5);

// ---------------------------------------------------------------------------
// 6. Exemple d'équivalence 2nd source (miroir de mpn_alternates)
// ---------------------------------------------------------------------------
MERGE (a1:Component {mpn: 'AMS1117-3.3'})
MERGE (a2:Component {mpn: 'MCP1700-3302E/TT'})
MERGE (a1)-[:ALTERNATE {verified: true, note: 'courant max 250 mA — verifier le budget'}]->(a2);
