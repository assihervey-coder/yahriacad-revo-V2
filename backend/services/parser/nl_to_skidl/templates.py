"""Bibliothèque de blocs fonctionnels paramétriques pour la génération SKiDL.

Chaque bloc fournit : un fragment de script SKiDL (template avec placeholders
{vdd} / {gnd} / {p}), les composants par défaut (ref, mpn, empreinte, prix) et
une explication d'architecture — consommé par nl_to_skidl (brique Circuitron).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Component  # noqa: E402


@dataclass
class BlockTemplate:
    """Bloc fonctionnel réutilisable — brique d'architecture matérielle."""

    key: str
    title: str
    keywords: Tuple[str, ...]
    skidl_template: str                       # placeholders : {vdd}, {gnd}, {p}
    explanation: str
    components: List[Component] = field(default_factory=list)


def _comp(ref: str, mpn: str, value: str, footprint: str, pins: int, price: float,
          block: str, width: float = 5.0, height: float = 5.0) -> Component:
    return Component(ref=ref, mpn=mpn, value=value, footprint=footprint, pins=pins,
                     width_mm=width, height_mm=height, price_usd=price,
                     functional_block=block)


MCU_STM32 = BlockTemplate(
    key="mcu_stm32",
    title="MCU STM32 (Cortex-M4)",
    keywords=("stm32", "mcu", "microcontroleur", "microcontroller", "f405", "f411", "cortex", "carte mere"),
    explanation=(
        "Le STM32F405 (LQFP-64) est le cerveau du design : périphériques riches "
        "(SPI/I2C/USB/SDIO), disponibilité et coût maîtrisés. Découplage 100 nF sur "
        "l'alimentation + broche NRST disponibles pour le debug SWD."
    ),
    skidl_template="""\
# --- Bloc MCU : STM32F405RGT6 (LQFP-64) --------------------------------------
mcu = Part('MCU_ST_STM32F4', 'STM32F405RGTx', footprint='Package_QFP:LQFP-64_10x10mm_P0.5mm')
mcu['VDD'] += {vdd}
mcu['VSS'] += {gnd}
mcu['NRST'] += nreset
c_dec{p} = Part('Device:C', 'C{p}1', value='100nF', footprint='Capacitor_SMD:C_0603_1608Metric')
c_dec{p}[1] += {vdd}
c_dec{p}[2] += {gnd}
""",
    components=[
        _comp("U1", "STM32F405RGT6", "STM32F405", "Package_QFP:LQFP-64_10x10mm_P0.5mm", 64, 6.50, "mcu", 10.0, 10.0),
        _comp("C1", "CL10B104KB8NNNC", "100nF", "Capacitor_SMD:C_0603_1608Metric", 2, 0.01, "passive", 1.6, 0.8),
    ],
)

LORA_MODEM = BlockTemplate(
    key="lora_modem",
    title="Modem LoRa (RFM95W / SX1276)",
    keywords=("lora", "rfm95", "sx1276", "sx1262", "868", "915", "433", "radio", "rf"),
    explanation=(
        "Le RFM95W (868 MHz) dialogue avec le MCU via SPI ; ligne d'antenne dédiée "
        "avec keepout imposé par le constraint_extractor pour préserver l'ROS."
    ),
    skidl_template="""\
# --- Bloc RF : modem LoRa RFM95W (SPI) ---------------------------------------
lora = Part('RF_Module', 'RFM95W-868S2', footprint='RF_Module:HOPERF_RFM9xW_SMD')
lora['SPI_MOSI'] += spi_mosi
lora['SPI_MISO'] += spi_miso
lora['SPI_SCK'] += spi_sck
lora['SPI_NSS'] += spi_nss
lora['ANT'] += antenna
""",
    components=[
        _comp("U2", "RFM95W-868S2", "RFM95W", "RF_Module:HOPERF_RFM9xW_SMD", 16, 8.00, "rf", 16.0, 16.0),
    ],
)

POWER_REGULATOR = BlockTemplate(
    key="power_regulator",
    title="Régulateur 3V3 (AMS1117)",
    keywords=("regulateur", "regulator", "ldo", "3v3", "5v", "alim", "buck", "power", "batterie", "battery", "lipo"),
    explanation=(
        "L'AMS1117-3.3 (SOT-223, 1 A) produit le rail 3V3 depuis le 5V USB/batterie ; "
        "capacités d'entrée/sortie de 10 µF pour la stabilité."
    ),
    skidl_template="""\
# --- Bloc alimentation : AMS1117-3.3 (5V -> 3V3) ------------------------------
reg = Part('Regulator_Linear', 'AMS1117-3.3', footprint='Package_TO_SOT_SMD:SOT-223-3_TabPin2')
reg['VI'] += v5v
reg['VO'] += {vdd}
reg['GND'] += {gnd}
c_in{p} = Part('Device:C', 'C{p}2', value='10uF', footprint='Capacitor_SMD:C_0805_2012Metric')
c_in{p}[1] += v5v
c_in{p}[2] += {gnd}
c_out{p} = Part('Device:C', 'C{p}3', value='10uF', footprint='Capacitor_SMD:C_0805_2012Metric')
c_out{p}[1] += {vdd}
c_out{p}[2] += {gnd}
""",
    components=[
        _comp("U3", "AMS1117-3.3", "AMS1117-3.3", "Package_TO_SOT_SMD:SOT-223-3_TabPin2", 3, 0.25, "power", 6.5, 3.5),
        _comp("C2", "CL21A106KAYNNNE", "10uF", "Capacitor_SMD:C_0805_2012Metric", 2, 0.03, "passive", 2.0, 1.25),
        _comp("C3", "CL21A106KAYNNNE", "10uF", "Capacitor_SMD:C_0805_2012Metric", 2, 0.03, "passive", 2.0, 1.25),
    ],
)

USB_INTERFACE = BlockTemplate(
    key="usb_interface",
    title="Interface USB 2.0 (connecteur + ESD)",
    keywords=("usb", "type-c", "otg", "cp2102", "uart-usb", "vbus"),
    explanation=(
        "Connecteur USB avec protection ESD USBLC6-2SC6 sur D+/D- ; le différentiel "
        "90 ohm est contraint par le constraint_extractor (classe USB2)."
    ),
    skidl_template="""\
# --- Bloc USB : connecteur + protection ESD -----------------------------------
usb = Part('Connector', 'USB_B_Micro', footprint='Connector_USB:USB_Micro-B_Molex_47346-0001')
usblc = Part('Power_Protection', 'USBLC6-2SC6', footprint='Package_TO_SOT_SMD:SOT-23-6')
usb['D+'] += usb_dp
usb['D-'] += usb_dm
usb['VBUS'] += v5v
usb['GND'] += {gnd}
usb_dp & usblc['I/O1']
usb_dm & usblc['I/O2']
""",
    components=[
        _comp("J1", "47346-0001", "USB_Micro-B", "Connector_USB:USB_Micro-B_Molex_47346-0001", 5, 0.60, "connector", 7.5, 5.0),
        _comp("U4", "USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6", 6, 0.35, "logic", 2.9, 1.6),
    ],
)

SENSOR_I2C = BlockTemplate(
    key="sensor_i2c",
    title="Capteur environnemental I2C (BME280)",
    keywords=("capteur", "sensor", "i2c", "imu", "bmp", "bme", "mpu", "sht", "sda", "scl"),
    explanation=(
        "BME280 sur le bus I2C (0x76) avec résistances de tirage 4,7 kΩ sur SDA/SCL "
        "— l'ERC vérifie leur présence (règle ERC-missing-pullup)."
    ),
    skidl_template="""\
# --- Bloc capteur : BME280 (I2C) ----------------------------------------------
sensor = Part('Sensor_Humidity', 'BME280', footprint='Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm')
sensor['SDA'] += sda
sensor['SCL'] += scl
sensor['VDD'] += {vdd}
sensor['GND'] += {gnd}
r_pull1{p} = Part('Device:R', 'R{p}1', value='4.7k', footprint='Resistor_SMD:R_0603_1608Metric')
r_pull2{p} = Part('Device:R', 'R{p}2', value='4.7k', footprint='Resistor_SMD:R_0603_1608Metric')
r_pull1{p}[1] += {vdd}; r_pull1{p}[2] += sda
r_pull2{p}[1] += {vdd}; r_pull2{p}[2] += scl
""",
    components=[
        _comp("U5", "BME280", "BME280", "Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm", 8, 4.20, "sensor", 2.5, 2.5),
        _comp("R2", "RC0603FR-074K7L", "4.7k", "Resistor_SMD:R_0603_1608Metric", 2, 0.005, "passive", 1.6, 0.8),
        _comp("R3", "RC0603FR-074K7L", "4.7k", "Resistor_SMD:R_0603_1608Metric", 2, 0.005, "passive", 1.6, 0.8),
    ],
)

MOTOR_DRIVER = BlockTemplate(
    key="motor_driver",
    title="Driver moteur (DRV8833)",
    keywords=("moteur", "motor", "drv8833", "l298", "esc", "servo", "brushless", "quadricoptere", "drone"),
    explanation=(
        "Le DRV8833 pilote deux moteurs brushed (1,5 A/canal) ; les sorties SW "
        "transportent du courant fort — pistes élargies gérées par le router."
    ),
    skidl_template="""\
# --- Bloc moteur : DRV8833 (double pont H) -------------------------------------
drv = Part('Driver_Motor', 'DRV8833PWP', footprint='Package_SO:HTSSOP-16-1EP_4.4x5mm_P0.65mm')
drv['IN1'] += motor_in1
drv['IN2'] += motor_in2
drv['OUT1'] += motor_out1
drv['OUT2'] += motor_out2
drv['VM'] += v5v
drv['GND'] += {gnd}
""",
    components=[
        _comp("U6", "DRV8833PWP", "DRV8833", "Package_SO:HTSSOP-16-1EP_4.4x5mm_P0.65mm", 16, 2.10, "power", 5.0, 5.0),
    ],
)

# Priorité d'assemblage : alimentation d'abord, MCU ensuite, périphériques ensuite
BLOCKS: Dict[str, BlockTemplate] = {
    "power_regulator": POWER_REGULATOR,
    "mcu_stm32": MCU_STM32,
    "lora_modem": LORA_MODEM,
    "usb_interface": USB_INTERFACE,
    "sensor_i2c": SENSOR_I2C,
    "motor_driver": MOTOR_DRIVER,
}

_BLOCK_PRIORITY = {key: index for index, key in enumerate(
    ["power_regulator", "mcu_stm32", "lora_modem", "usb_interface", "sensor_i2c", "motor_driver"])}


def get_block(key: str) -> BlockTemplate:
    """Accès typé à un bloc — KeyError explicite si la clé est inconnue."""
    if key not in BLOCKS:
        raise KeyError(f"bloc fonctionnel inconnu : {key} (connus : {sorted(BLOCKS)})")
    return BLOCKS[key]


def all_blocks() -> List[BlockTemplate]:
    """Blocs triés par priorité d'assemblage."""
    return sorted(BLOCKS.values(), key=lambda b: _BLOCK_PRIORITY[b.key])
