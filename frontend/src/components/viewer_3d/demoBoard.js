/* Design de démonstration — carte contrôleur de drone STM32 + LoRa.
 *
 * Généré localement (aucune requête réseau) pour démontrer le viewer, le HUD
 * et la sélection sans backend. Forme identique à GET /design/{id}/state
 * (Board du design_model : components / placements / nets / zones).
 */

function mulberry32(seed) {
  let a = seed | 0;
  return function next() {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Segment de piste (même forme que common/design_model.Segment). */
function seg(net, x1, y1, x2, y2, layer, width = 0.25, isVia = false) {
  return { net, x1_mm: x1, y1_mm: y1, x2_mm: x2, y2_mm: y2, layer, width_mm: width, is_via: isVia };
}

/** Chemin orthogonal 2 segments (coude à mi-chemin), avec via optionnel au départ. */
function run(net, x1, y1, x2, y2, layer, width = 0.25, midRatio = 0.5) {
  const midX = x1 + (x2 - x1) * midRatio;
  return [
    seg(net, x1, y1, midX, y1, layer, width),
    seg(net, midX, y1, midX, y2, layer, width),
    seg(net, midX, y2, x2, y2, layer, width),
  ];
}

function padsGrid(count, pitch, cols) {
  const pads = [];
  for (let i = 0; i < count; i += 1) {
    const col = i % cols;
    const row = Math.floor(i / cols);
    pads.push({
      name: `p${i + 1}`,
      x_mm: (col - (cols - 1) / 2) * pitch,
      y_mm: (row % 2 === 0 ? -1 : 1) * pitch,
      diameter_mm: 0.5,
    });
  }
  return pads;
}

export function buildDemoBoard() {
  const rand = mulberry32(20240517);

  const components = {
    U1: {
      ref: 'U1',
      mpn: 'STM32H743VIT6',
      value: 'MCU Cortex-M7 480 MHz',
      footprint: 'Package_QFP:LQFP-100_14x14mm_P0.5mm',
      pins: 100,
      width_mm: 14,
      height_mm: 14,
      power_w: 1.1,
      price_usd: 11.4,
      stock: 320,
      functional_block: 'mcu',
      pads: padsGrid(25, 1.1, 5),
    },
    U2: {
      ref: 'U2',
      mpn: 'SX1276IMLTRT',
      value: 'LoRa 868/915 MHz',
      footprint: 'RF_Module:HFAN-1.1-SMD',
      pins: 16,
      width_mm: 16,
      height_mm: 12,
      power_w: 0.35,
      price_usd: 4.85,
      stock: 140,
      functional_block: 'rf',
      pads: padsGrid(8, 1.6, 4),
    },
    ANT1: {
      ref: 'ANT1',
      mpn: '2450AT18B100E',
      value: 'Antenne céramique',
      footprint: 'Antenna:Antenna_Chip',
      pins: 2,
      width_mm: 5,
      height_mm: 2.4,
      price_usd: 0.62,
      stock: 500,
      functional_block: 'rf',
      pads: padsGrid(2, 1.6, 1),
    },
    U4: {
      ref: 'U4',
      mpn: 'TPS62840DLCR',
      value: 'Buck 1.8-6.5 V → 3.3 V',
      footprint: 'Package_DFN_QFN:DFN-8-1EP_2x2mm',
      pins: 8,
      width_mm: 2.2,
      height_mm: 2.2,
      power_w: 0.4,
      price_usd: 1.35,
      stock: 800,
      functional_block: 'power',
      pads: padsGrid(4, 0.9, 2),
    },
    U5: {
      ref: 'U5',
      mpn: 'MCP1700-3302E',
      value: 'LDO 3.3 V 250 mA',
      footprint: 'Package_TO_SOT_SMD:SOT-23',
      pins: 3,
      width_mm: 2.9,
      height_mm: 1.6,
      price_usd: 0.29,
      stock: 1500,
      functional_block: 'power',
      pads: padsGrid(3, 1.0, 3),
    },
    U6: {
      ref: 'U6',
      mpn: 'LSM6DSRTR',
      value: 'IMU 6 axes',
      footprint: 'Package_LGA:LGA-14_3x2.5mm',
      pins: 14,
      width_mm: 3,
      height_mm: 2.5,
      price_usd: 2.1,
      stock: 600,
      functional_block: 'sensor',
      pads: padsGrid(7, 0.7, 4),
    },
    U7: {
      ref: 'U7',
      mpn: 'BMP390L1',
      value: 'Baromètre',
      footprint: 'Package_LGA:LGA-10_2x2mm',
      pins: 10,
      width_mm: 2,
      height_mm: 2,
      price_usd: 2.85,
      stock: 400,
      functional_block: 'sensor',
      pads: padsGrid(5, 0.6, 3),
    },
    U8: {
      ref: 'U8',
      mpn: 'W25Q128JVSIQ',
      value: 'Flash 128 Mbit SPI',
      footprint: 'Package_SO:SOIC-8_3.9x4.9mm',
      pins: 8,
      width_mm: 4.9,
      height_mm: 3.9,
      price_usd: 1.05,
      stock: 900,
      functional_block: 'memory',
      pads: padsGrid(4, 1.2, 2),
    },
    J1: {
      ref: 'J1',
      mpn: 'USB4105-GF-A',
      value: 'USB-C receptacle',
      footprint: 'Connector_USB:USB_C_Receptacle_GCT_USB4105',
      pins: 12,
      width_mm: 9,
      height_mm: 7.3,
      price_usd: 1.2,
      stock: 300,
      functional_block: 'io',
      pads: padsGrid(6, 1.1, 3),
    },
    J2: {
      ref: 'J2',
      mpn: 'SM06B-SRSS-TB',
      value: 'JST-GH 6 broches',
      footprint: 'Connector_JST:JST_GH_SM06B-SRSS-TB',
      pins: 6,
      width_mm: 7.4,
      height_mm: 3.5,
      price_usd: 0.55,
      stock: 450,
      functional_block: 'io',
      pads: padsGrid(3, 1.25, 3),
    },
  };
  // condensateurs de découplage du bloc power
  for (let i = 1; i <= 6; i += 1) {
    components[`C${i}`] = {
      ref: `C${i}`,
      mpn: 'CL10B104KB8NNNC',
      value: '100 nF 25 V X7R',
      footprint: 'Capacitor_SMD:C_0603_1608Metric',
      pins: 2,
      width_mm: 1.6,
      height_mm: 0.8,
      price_usd: 0.008,
      stock: 12000,
      functional_block: 'power',
      pads: padsGrid(2, 0.9, 1),
    };
  }

  const P = (ref, x, y, rot = 0, locked = false) => ({
    ref,
    x_mm: x,
    y_mm: y,
    rotation_deg: rot,
    layer: 0,
    locked,
  });

  // coordonnées en mm, origine = coin bas-gauche d'une carte 90 × 60
  const placements = {
    U1: P('U1', 42, 30, 0),
    U2: P('U2', 73, 42, 0),
    ANT1: P('ANT1', 84, 52, 90),
    U4: P('U4', 10, 40, 0),
    U5: P('U5', 16, 24, 0),
    U6: P('U6', 55, 18, 90),
    U7: P('U7', 30, 16, 0),
    U8: P('U8', 26, 44, 0),
    J1: P('J1', 42, 6, 0),
    J2: P('J2', 74, 10, 0),
    C1: P('C1', 14, 44, 90),
    C2: P('C2', 7, 36, 0),
    C3: P('C3', 19, 19, 90),
    C4: P('C4', 34, 38, 90),
    C5: P('C5', 63, 40, 0),
    C6: P('C6', 68, 33, 0),
  };

  const nets = {
    'SPI1_SCK': { name: 'SPI1_SCK', net_class: 'SPI', connections: [['U1', 'p5'], ['U2', 'p2']], routed_segments: run('SPI1_SCK', 49, 34, 68, 43, 0, 0.25, 0.6) },
    'SPI1_MISO': { name: 'SPI1_MISO', net_class: 'SPI', connections: [['U1', 'p6'], ['U2', 'p3']], routed_segments: run('SPI1_MISO', 49, 32, 67, 44, 0, 0.25, 0.4) },
    'SPI1_MOSI': { name: 'SPI1_MOSI', net_class: 'SPI', connections: [['U1', 'p7'], ['U2', 'p4']], routed_segments: run('SPI1_MOSI', 50, 31, 66, 45, 3, 0.25, 0.5) },
    'RF_ANT': { name: 'RF_ANT', net_class: 'RF_50R', connections: [['U2', 'p1'], ['ANT1', 'p1']], routed_segments: [seg('RF_ANT', 80, 47, 80, 52, 0, 0.6), seg('RF_ANT', 80, 52, 84, 52, 0, 0.6)] },
    'USB_DP': { name: 'USB_DP', net_class: 'USB', connections: [['J1', 'p2'], ['U1', 'p20']], routed_segments: run('USB_DP', 41, 9, 40, 23, 0, 0.35, 0.5) },
    'USB_DM': { name: 'USB_DM', net_class: 'USB', connections: [['J1', 'p3'], ['U1', 'p21']], routed_segments: run('USB_DM', 43, 9, 42, 23, 0, 0.35, 0.5) },
    'I2C1_SDA': { name: 'I2C1_SDA', net_class: 'I2C', connections: [['U1', 'p30'], ['U6', 'p2'], ['U7', 'p3']], routed_segments: [seg('I2C1_SDA', 44, 24, 54, 24, 0, 0.25), seg('I2C1_SDA', 54, 24, 54, 18, 0, 0.25), seg('I2C1_SDA', 44, 24, 30, 24, 0, 0.25), seg('I2C1_SDA', 30, 24, 30, 17, 0, 0.25)] },
    'I2C1_SCL': { name: 'I2C1_SCL', net_class: 'I2C', connections: [['U1', 'p31'], ['U6', 'p1'], ['U7', 'p4']], routed_segments: [seg('I2C1_SCL', 46, 26, 56, 26, 0, 0.25), seg('I2C1_SCL', 56, 26, 56, 18, 0, 0.25), seg('I2C1_SCL', 46, 26, 32, 26, 0, 0.25), seg('I2C1_SCL', 32, 26, 32, 17, 0, 0.25)] },
    'QSPI_CLK': { name: 'QSPI_CLK', net_class: 'QSPI', connections: [['U1', 'p40'], ['U8', 'p4']], routed_segments: run('QSPI_CLK', 36, 36, 27, 44, 0, 0.25, 0.5) },
    'VDD_3V3': { name: 'VDD_3V3', net_class: 'power', connections: [['U4', 'p3'], ['U1', 'p10'], ['U2', 'p8']], routed_segments: [seg('VDD_3V3', 11, 41, 20, 41, 2, 0.5), seg('VDD_3V3', 20, 41, 20, 33, 2, 0.5), seg('VDD_3V3', 20, 33, 34, 33, 2, 0.5), seg('VDD_3V3', 34, 33, 34, 30, 2, 0.5), { ...seg('VDD_3V3', 20, 41, 20, 41, 2, 0.5), is_via: true }, seg('VDD_3V3', 20, 33, 68, 33, 2, 0.5), seg('VDD_3V3', 68, 33, 68, 40, 2, 0.5)] },
    'GND': { name: 'GND', net_class: 'power', connections: [['U1', 'p1'], ['J1', 'p1']], routed_segments: [seg('GND', 8, 44, 8, 10, 3, 0.8), seg('GND', 8, 10, 38, 10, 3, 0.8)] },
  };
  // vias de couture GND (points is_via)
  for (let i = 0; i < 14; i += 1) {
    nets.GND.routed_segments.push(
      seg('GND', 6 + rand() * 78, 6 + rand() * 48, 6 + rand() * 78, 6 + rand() * 48, 0, 0.3, true)
    );
  }

  return {
    project_id: 'demo-drone-stm32-lora',
    version: 3,
    pipeline_step: 6,
    board: {
      width_mm: 90,
      height_mm: 60,
      layers: [
        { index: 0, name: 'F.Cu' },
        { index: 1, name: 'GND' },
        { index: 2, name: 'PWR' },
        { index: 3, name: 'B.Cu' },
      ],
      components,
      placements,
      nets,
      zones: [
        { name: 'keepout_rf', x_min_mm: 66, y_min_mm: 48, x_max_mm: 88, y_max_mm: 58, kind: 'keepout' },
        { name: 'thermal_buck', x_min_mm: 4, y_min_mm: 34, x_max_mm: 18, y_max_mm: 48, kind: 'thermal', max_temp_c: 85 },
        { name: 'bloc_mcu', x_min_mm: 32, y_min_mm: 20, x_max_mm: 52, y_max_mm: 40, kind: 'functional' },
      ],
    },
  };
}
