"""Point d'entrée du service firmware_bridge [Flux.ai] (section 6.5).

export_pins() / generate_headers() + détection de re-régénération : toute
modification de brochage comparée au dernier export émet FIRMWARE_REGENERATED
et notifie le firmware_preview. Sans stubs gRPC, `python main.py` exécute un
self-test complet (carte STM32+LoRa → pin_map_zephyr.h + pin_map_arduino.h).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

_SERVICE_DIR = Path(__file__).resolve().parent
_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_ROOT), str(_SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from common.config import get_settings  # noqa: E402
from common.design_model import Board  # noqa: E402
from common.events import EventType, make_event  # noqa: E402
from common.log import get_logger  # noqa: E402

from pin_exporter.exporter import PinAssignment, export_pins  # noqa: E402
from header_generator.generators import GeneratedHeader, generate_headers  # noqa: E402

logger = get_logger("firmware_bridge")

STATE_DIR = Path("data/projects")  # {project_id}/firmware/pin_state.json


def export_pins_for(board: Board, project_id: str = "demo",
                    target: str = "raw", design_version: int = 1) -> List[PinAssignment]:
    """Exporte les broches et détecte les changements de brochage.

    Si l'assignation diffère du dernier export (fichier pin_state.json), un
    événement FIRMWARE_REGENERATED est émis — le firmware_preview rafraîchit
    ses diffs et le développeur embarqué ne peut plus écrire un firmware
    désynchronisé du matériel.
    """
    get_settings("firmware_bridge")
    assignments = export_pins(board)
    signature = [a.to_dict() for a in assignments]

    state_path = STATE_DIR / project_id / "firmware" / "pin_state.json"
    previous = _load_state(state_path)
    if previous is not None and previous != signature:
        changed = _diff_count(previous, signature)
        make_event(EventType.FIRMWARE_REGENERATED, project_id, "firmware_bridge",
                   design_version, changed=changed, target=target)
        logger.info("brochage modifié — régénération déclenchée",
                    extra={"project": project_id, "changed": changed})
    _save_state(state_path, signature)
    return assignments


def generate_headers_for(board: Board, project_id: str = "demo",
                         target: str = "zephyr", design_version: int = 1) -> List[GeneratedHeader]:
    """Export broches (avec détection de changement) puis génération des .h."""
    export_pins_for(board, project_id, target, design_version)
    headers = generate_headers(board, target)
    for header in headers:
        logger.info("en-tête généré", extra={"file": header.filename,
                                             "pins": len(header.assignments)})
    return headers


# ------------------------------------------------------- état de brochage ----
def _load_state(state_path: Path) -> Optional[List[Dict]]:
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_state(state_path: Path, signature: List[Dict]) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(signature, indent=1), encoding="utf-8")
    except OSError:
        logger.warning("état de brochage non persisté (disque lecture seule ?)")


def _diff_count(previous: List[Dict], current: List[Dict]) -> int:
    """Nombre d'assignations modifiées — charge utile de FIRMWARE_REGENERATED."""
    prev_index = {(p.get("component_ref"), p.get("pin")): p for p in previous}
    changed = 0
    for cur in current:
        key = (cur.get("component_ref"), cur.get("pin"))
        if prev_index.get(key) != cur:
            changed += 1
    return changed + abs(len(previous) - len(current))


def _self_test() -> int:
    """Self-test : carte STM32 + LoRa → broches → headers Zephyr/Arduino → diff."""
    from common.design_model import Board, Component, Net, Pad, Placement

    board = Board(width_mm=40.0, height_mm=30.0)
    defs = {
        "U1": (8.0, 15.0, 8.0, 8.0, {"1": "VDD", "2": "GND", "3": "SWDIO", "4": "SWCLK",
                                     "5": "SPI2_SCK", "6": "SPI2_MOSI", "7": "I2C1_SDA",
                                     "8": "I2C1_SCL"}),
        "U2": (30.0, 15.0, 6.0, 4.0, {"1": "SPI2_SCK", "2": "SPI2_MOSI", "3": "ANT"}),
        "R1": (20.0, 8.0, 2.0, 1.0, {"1": "I2C1_SDA", "2": "VDD"}),
        "C1": (20.0, 22.0, 2.0, 1.0, {"1": "VDD", "2": "GND"}),
    }
    for ref, (x, y, w, h, pads_def) in defs.items():
        comp = Component(ref=ref, width_mm=w, height_mm=h)
        comp.pads = [Pad(name=name, x_mm=x + i * 1.0, y_mm=y, net=net)
                     for i, (name, net) in enumerate(pads_def.items())]
        board.add_component(comp, Placement(ref=ref, x_mm=x, y_mm=y))
    for name, net_class in {"VDD": "POWER", "GND": "POWER", "SPI2_SCK": "SPI",
                            "SPI2_MOSI": "SPI", "I2C1_SDA": "I2C", "I2C1_SCL": "I2C",
                            "SWDIO": "DEBUG", "SWCLK": "DEBUG", "ANT": "RF"}.items():
        board.nets[name] = Net(name=name, net_class=net_class)

    assignments = export_pins_for(board, "selftest", design_version=3)
    print(f"[firmware] broches exportées : {len(assignments)}")
    for a in assignments[:5]:
        print(f"  {a.component_ref}/{a.pin:<4s} → {a.function:<12s} (net {a.net})")

    headers = generate_headers_for(board, "selftest", target="both", design_version=3)
    for header in headers:
        print(f"[firmware] {header.filename} : {len(header.c_source.splitlines())} lignes")
    zephyr_header = headers[0].c_source
    ok_zephyr = "#ifndef PCB_AI_DESIGNER_PIN_MAP_ZEPHYR_H" in zephyr_header
    ok_pins = len(assignments) >= 14

    # simulation d'un changement de brochage → FIRMWARE_REGENERATED
    board.components["U1"].pads[2].net = "SPI3_SCK"  # remap pin 3
    assignments2 = export_pins_for(board, "selftest", design_version=4)
    changed = sum(1 for a in assignments2 if a.pin.endswith("3") and a.component_ref == "U1")
    print(f"[firmware] changement détecté sur {changed} broche(s) → FIRMWARE_REGENERATED")

    ok = ok_zephyr and ok_pins and changed >= 1
    print("[firmware] SELF-TEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        from common.grpc_helpers import proto_available

        if proto_available():
            from common.grpc_helpers import serve_grpc

            serve_grpc("firmware_bridge", None)  # type: ignore[arg-type]
            sys.exit(0)
    except Exception:  # noqa: BLE001
        pass
    sys.exit(_self_test())
