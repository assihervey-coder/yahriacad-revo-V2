"""Point d'entrée du service parser — orchestre les 3 sous-modules (section 6.1).

Fonctions importables : import_file(), natural_language_to_skidl(),
extract_constraints(). Sans stubs gRPC, `python main.py` exécute un self-test
déterministe sur des échantillons SPICE / KiCad / NL puis sort avec le code 0.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

_SERVICE_DIR = Path(__file__).resolve().parent
_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_ROOT), str(_SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from common.bus import ConstraintMessage, InMemoryConstraintBus  # noqa: E402
from common.config import get_settings  # noqa: E402
from common.design_model import Board  # noqa: E402
from common.events import EventType, WorkflowStep, make_event  # noqa: E402
from common.log import get_logger  # noqa: E402

from netlist_parser.kicad_sch_parser import parse_kicad_sch  # noqa: E402
from netlist_parser.normalizer import ImportReport, detect_format, normalize_import  # noqa: E402
from netlist_parser.spice_parser import parse_spice  # noqa: E402
from nl_to_skidl.nl_to_skidl import SkidlScript  # noqa: E402
from nl_to_skidl.nl_to_skidl import natural_language_to_skidl as _nl_to_skidl  # noqa: E402
from constraint_extractor.extractor import extract_constraints as _extract_constraints  # noqa: E402
from constraint_extractor.extractor import publish as _publish_constraints  # noqa: E402

logger = get_logger("parser")


def import_file(design_id: str, filename: str, content_bytes: bytes,
                format_hint: str = "auto") -> ImportReport:
    """Importe un fichier (SPICE ou schéma KiCad) et le normalise vers un Board."""
    fmt = detect_format(filename, content_bytes, format_hint)
    text = content_bytes.decode("utf-8", errors="replace")
    try:
        if fmt == "kicad_sch":
            parsed = parse_kicad_sch(text)
            report = normalize_import([inst.component for inst in parsed.components],
                                      parsed.net_connections, parsed.wire_hints,
                                      parsed.warnings)
        else:
            parsed = parse_spice(text)
            report = normalize_import(parsed.components, parsed.net_connections, None,
                                      parsed.warnings)
    except Exception as exc:  # entrée invalide : rapport explicite plutôt qu'exception
        logger.warning("import échoué", extra={"source_file": filename, "format": fmt,
                                               "error": str(exc)})
        return ImportReport(False, 0, 0, [f"parse {fmt} impossible : {exc}"], Board())

    event = make_event(EventType.STEP_PROGRESS, design_id, emitter="parser",
                       step=WorkflowStep.NL_TO_SKIDL.value,
                       detail=f"import {fmt} : {report.components_imported} composants")
    logger.info("événement émis", extra=event.to_json())
    return report


def natural_language_to_skidl(text: str, llm=None) -> SkidlScript:
    """Requête en langage naturel → script SKiDL (délegation à nl_to_skidl)."""
    script = _nl_to_skidl(text, llm=llm)
    event = make_event(EventType.SKIDL_GENERATED, "local", emitter="parser",
                       blocks=script.functional_blocks)
    logger.info("événement émis", extra=event.to_json())
    return script


def extract_constraints(spec_text: str, project_id: str = "",
                        bus: Optional[InMemoryConstraintBus] = None) -> List[ConstraintMessage]:
    """Extrait les contraintes du texte (et les publie sur le bus si fourni)."""
    messages = _extract_constraints(spec_text, project_id)
    if bus is not None:
        _publish_constraints(bus, messages)
    return messages


def _bootstrap_grpc() -> bool:
    """Tente de servir le service en gRPC ; False si les stubs sont absents."""
    try:
        from common.grpc_helpers import proto_available, serve_grpc
        if not proto_available():
            logger.warning("stubs gRPC absents (make proto non exécuté) — lancement du self-test")
            return False

        def add_servicer(_servicer: object, server: object) -> None:
            from proto_gen.parser.v1 import parser_pb2_grpc
            parser_pb2_grpc.add_ParserServicer_to_server(_servicer, server)  # type: ignore[attr-defined]

        serve_grpc("parser", add_servicer)
        return True
    except SystemExit:
        raise
    except Exception as exc:
        logger.warning("bootstrap gRPC impossible — bascule self-test", extra={"error": str(exc)})
        return False


def _self_test() -> int:
    """Démontre la chaîne d'import complète sur des échantillons déterministes."""
    settings = get_settings("parser")
    logger.info("self-test du parser", extra={"service_name": settings.service_name})

    # 1) import d'une netliste SPICE (sous-circuit, commentaires, continuation)
    spice = """* Netlist exemple — carte drone
.include models/stm32.lib
.subckt LDO IN OUT GND
  R1 1 2 10k
  C1 2 0 100n
.ends

X1 3 4 LDO          ; instance du sous-circuit
U1 3 4 5 6 STM32F405
R1 5 7 4k7
C2 7 0 100n
"""
    report_spice = import_file("selftest", "drone.net", spice.encode("utf-8"))
    assert report_spice.ok, "import SPICE attendu valide"
    assert report_spice.components_imported >= 6, "6 composants attendus (dont sous-circuit préfixé)"
    assert "GND" in report_spice.board.nets, "la masse 0 doit être renommée GND"
    assert any("flottant" in w for w in report_spice.warnings), "nets 1/2/6 flottants attendus"

    # 2) import d'un schéma KiCad minimal
    kicad = """(kicad_sch (version 20230121) (generator eeschema)
  (symbol (lib_id "Device:R") (at 50.8 40.64 0) (unit 1)
    (property "Reference" "R1" (at 52.7 39.9 0))
    (property "Value" "10k" (at 50.8 42.3 0)))
  (symbol (lib_id "RF_Module:RFM95W-868S2") (at 120 45 0) (unit 1)
    (property "Reference" "U1" (at 120 32 0))
    (property "Value" "RFM95W-868S2" (at 120 58 0))
    (pin "1" (uuid 00000000-0000-0000-0000-000000000001))
    (pin "2" (uuid 00000000-0000-0000-0000-000000000002)))
  (wire (pts (xy 40.64 40.64) (xy 50.8 40.64)) (stroke (width 0) (type default)))
)
"""
    report_kicad = import_file("selftest", "module.kicad_sch", kicad.encode("utf-8"))
    assert report_kicad.ok, "import KiCad attendu valide"
    assert report_kicad.components_imported == 2, "2 symboles attendus"
    assert any("connectivité non résolue" in w for w in report_kicad.warnings)

    # 3) langage naturel → SKiDL
    script = natural_language_to_skidl(
        "je veux une carte drone avec un stm32, un modem lora, des capteurs i2c "
        "et un régulateur 5V vers 3.3V")
    assert "from skidl import" in script.python_source, "le script doit importer skidl"
    assert script.functional_blocks, "au moins un bloc fonctionnel attendu"
    assert "generate_netlist()" in script.python_source

    # 4) extraction + publication des contraintes sur le bus
    bus = InMemoryConstraintBus()
    spec = ("Interface USB2 en 90 ohm différentiel, paires DDR appariées ±5 mm, "
            "keepout de 3 mm près de l'antenne, température max 80°C, "
            "le rail 5V supporte jusqu'à 3A.")
    messages = extract_constraints(spec, project_id="selftest", bus=bus)
    kinds = {m.kind.name for m in messages}
    assert "IMPEDANCE_TARGET" in kinds, "contrainte d'impédance USB2/DDR attendue"
    assert "KEEPOUT_ZONE" in kinds and "THERMAL_ZONE_UPDATE" in kinds
    assert "LENGTH_MATCH_RULE" in kinds and "CURRENT_BUDGET" in kinds
    assert bus.published_count == len(messages), "toutes les contraintes doivent être publiées"
    assert any(k.startswith("impedance/") for k in bus.latest()), "dernière valeur retenue par clé"

    print(f"[parser] SPICE   : {report_spice.components_imported} composants, "
          f"{report_spice.nets_imported} nets, {len(report_spice.warnings)} avertissements")
    print(f"[parser] KiCad   : {report_kicad.components_imported} composants, "
          f"{len(report_kicad.warnings)} avertissements")
    print(f"[parser] SKiDL   : blocs {script.functional_blocks}")
    print(f"[parser] Contraintes : {len(messages)} messages, bus latence OK, "
          f"clés={sorted(bus.latest())[:4]}...")
    print("[parser] self-test : OK")
    return 0


if __name__ == "__main__":
    if _bootstrap_grpc():
        raise SystemExit(0)  # serveur gRPC démarré — arrêt propre via SIGTERM
    try:
        raise SystemExit(_self_test())
    except AssertionError as error:
        print(f"[parser] self-test ÉCHOUÉ : {error}")
        raise SystemExit(1)
