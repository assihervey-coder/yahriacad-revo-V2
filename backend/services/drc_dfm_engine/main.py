"""Point d'entrée du service drc_dfm_engine (section 6.4).

check() combine design_rules (géométrie) + manufacturing_rules (profil usine)
+ erc_executor [Circuitron] dans un CheckReply unique : pass, score composite,
violations. Sans stubs gRPC, `python main.py` exécute un self-test complet.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_SERVICE_DIR = Path(__file__).resolve().parent
_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_ROOT), str(_SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from common.config import get_settings  # noqa: E402
from common.design_model import Board  # noqa: E402
from common.log import get_logger  # noqa: E402

from design_rules.checker import DrcReport, Violation, check_design_rules  # noqa: E402
from manufacturing_rules.profiles import FactoryProfile, get_profile, check_factory  # noqa: E402
from erc_executor.erc import erc_summary, run_erc  # noqa: E402

logger = get_logger("drc_dfm_engine")


@dataclass
class CheckReply:
    """Réponse contractuelle du service (proto drc_dfm.v1)."""

    pass_: bool
    score: float
    violations: List[Violation] = field(default_factory=list)
    erc_errors: List[Violation] = field(default_factory=list)
    factory_profile: str = "internal"

    def to_json(self) -> dict:
        return {
            "pass": self.pass_,
            "score": self.score,
            "factory_profile": self.factory_profile,
            "violations": [v.to_json() for v in self.violations],
            "erc_errors": [v.to_json() for v in self.erc_errors],
        }


def check(board: Board, project_id: str = "demo", factory_profile: str = "internal",
          include_erc: bool = False, skidl_source: Optional[str] = None,
          design_version: int = 1) -> CheckReply:
    """Vérification complète : design + manufacturing (+ ERC optionnel).

    Le score composite (0..100) est la métrique officielle consommée par le
    keeper_logic de l'optimiseur nocturne et par le benchmark vs Quilter.
    """
    get_settings("drc_dfm_engine")
    profile: FactoryProfile = get_profile(factory_profile)
    report: DrcReport = check_design_rules(board)
    factory_violations = check_factory(board, profile)
    report.violations.extend(factory_violations)

    erc_errors: List[Violation] = []
    if include_erc:
        erc_errors = run_erc(board, project_id, skidl_source, design_version)

    has_error = any(v.severity == "error" for v in report.violations) or \
        any(v.severity == "error" for v in erc_errors)
    score = report.score if not erc_errors else max(
        0.0, report.score - 6.0 * len([e for e in erc_errors if e.severity == "error"]))

    reply = CheckReply(pass_=not has_error, score=round(score, 2),
                       violations=report.violations, erc_errors=erc_errors,
                       factory_profile=profile.name)
    logger.info("vérification DRC/DFM terminée",
                extra={"project": project_id, "pass": reply.pass_, "score": reply.score,
                       "violations": len(reply.violations), "erc": len(reply.erc_errors)})
    return reply


def _self_test() -> int:
    """Self-test : mini-carte avec violations volontaires (chevauchement, keepout)."""
    from common.design_model import Board, Component, Net, Pad, Placement, Zone

    board = Board(width_mm=30.0, height_mm=20.0)
    board.zones.append(Zone(name="keepout_rf", x_min_mm=22.0, y_min_mm=0.0,
                            x_max_mm=30.0, y_max_mm=8.0, kind="keepout"))
    for ref, (x, y, w, h) in {"U1": (5, 10, 8, 8), "U2": (25, 3, 6, 4),
                              "R1": (16, 14, 2, 1), "C1": (16, 17, 2, 1)}.items():
        comp = Component(ref=ref, width_mm=w, height_mm=h)
        comp.pads = [Pad(name="1", x_mm=x, y_mm=y, net="VDD"), Pad(name="2", x_mm=x + 1, y_mm=y, net="GND")]
        board.add_component(comp, Placement(ref=ref, x_mm=x, y_mm=y))
    board.nets["VDD"] = Net(name="VDD", connections=[("U1", "1"), ("R1", "1")], net_class="POWER")
    board.nets["GND"] = Net(name="GND", connections=[("U1", "2"), ("C1", "2")])

    reply = check(board, project_id="selftest", factory_profile="pcbway", include_erc=True,
                  skidl_source="from skidl import *\nNet('GND')\nPart('Device:R')")
    print(f"[drc] pass={reply.pass_} score={reply.score} profil={reply.factory_profile}")
    print(f"[drc] violations: {len(reply.violations)} — erc_errors: {len(reply.erc_errors)}")
    for v in reply.violations[:6]:
        print(f"  - {v.rule_id}: {v.message}")
    for v in reply.erc_errors[:6]:
        print(f"  - ERC {v.rule_id}: {v.message}")
    n_err, n_warn, counts = erc_summary(reply.violations + reply.erc_errors)
    print(f"[drc] répartition : {n_err} erreurs, {n_warn} avertissements — {counts}")
    ok = not reply.pass_ and reply.score < 100 and len(reply.erc_errors) > 0
    print("[drc] SELF-TEST", "OK (violations bien détectées)" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        from common.grpc_helpers import proto_available

        if proto_available():
            from common.grpc_helpers import serve_grpc

            serve_grpc("drc_dfm_engine", None)  # type: ignore[arg-type]
            sys.exit(0)
    except Exception:  # noqa: BLE001
        pass
    sys.exit(_self_test())
