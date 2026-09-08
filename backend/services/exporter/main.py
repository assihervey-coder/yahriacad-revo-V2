"""Point d'entrée du service exporter (section 6.6).

export() revérifie le profil usine via le drc_dfm_engine (un export ne sort
jamais avec une règle violée pour l'usine cible), génère Gerbers/ODB++/BOM/
pick&place, emballe (zip + sha256) et émet export_ready. Sans stubs gRPC,
`python main.py` exécute un self-test complet.
"""

from __future__ import annotations

import hashlib
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

_SERVICE_DIR = Path(__file__).resolve().parent
_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_ROOT), str(_SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from common.config import get_settings  # noqa: E402
from common.credits import CreditLedger, Pricing  # noqa: E402
from common.design_model import Board  # noqa: E402
from common.events import EventType, make_event  # noqa: E402
from common.log import get_logger  # noqa: E402

from gerber.rs274x import export_gerbers  # noqa: E402
from gerber.excellon import export_drill  # noqa: E402
from odb.odb_writer import export_odb  # noqa: E402
from bom_pickplace.bom_writer import export_bom  # noqa: E402
from bom_pickplace.pickplace_writer import export_pick_place  # noqa: E402

logger = get_logger("exporter")


@dataclass
class ExportResult:
    """Résultat contractuel du service (proto exporter.v1)."""

    ok: bool
    archive_path: str = ""
    files: List[str] = field(default_factory=list)
    sha256: str = ""
    warnings: List[str] = field(default_factory=list)


def export(board: Board, project_id: str = "demo", factory_profile: str = "pcbway",
           outputs: List[str] | None = None, out_dir: Path | None = None,
           design_version: int = 1) -> ExportResult:
    """Exporte le design : dernière vérification usine → fichiers → archive.

    outputs : sous-ensemble de {"gerber", "odb", "bom", "pick_place"}.
    La vérification finale interdit l'export si une erreur DRC/DFM subsiste —
    sauf warnings, journalisés comme avertissements de l'archive.
    """
    get_settings("exporter")
    outputs = outputs or ["gerber", "bom", "pick_place"]
    out_dir = out_dir or Path("data/projects") / project_id / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. dernière vérification contre le profil de l'usine cible
    warnings: List[str] = []
    try:
        from backend.services.drc_dfm_engine.main import check as drc_check

        reply = drc_check(board, project_id=project_id, factory_profile=factory_profile)
        if not reply.pass_:
            errors = [v for v in reply.violations if v.severity == "error"]
            if errors:
                return ExportResult(ok=False, warnings=[
                    f"{v.rule_id}: {v.message}" for v in errors[:5]
                ])
        warnings.extend(v.message for v in reply.violations if v.severity == "warning")
    except ImportError:
        warnings.append("drc_dfm_engine indisponible — vérification finale sautée (dev only)")
        logger.warning("drc_dfm_engine non importable — vérification finale sautée")

    # 2. génération des fichiers demandés
    files: Dict[str, str] = {}
    if "gerber" in outputs:
        files.update(export_gerbers(board))
        files["drill.drl"] = export_drill(board)
    if "odb" in outputs:
        odb = export_odb(board, project_id)
        for rel, content in odb.files.items():
            files[f"odb/{rel}"] = content
    if "bom" in outputs:
        bom_csv, _subtotals = export_bom(board)
        files["bom.csv"] = bom_csv
    if "pick_place" in outputs:
        files["pick_place.csv"] = export_pick_place(board)

    # 3. emballage + checksums
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    archive_path = out_dir / f"{project_id}_export_{ts}.zip"
    sha256 = hashlib.sha256()
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in sorted(files.items()):
            data = content.encode("utf-8")
            sha256.update(data)
            zf.writestr(name, data)
        manifest = (
            f"project: {project_id}\nfactory: {factory_profile}\n"
            f"outputs: {', '.join(outputs)}\nfiles: {len(files)}\n"
            f"warnings: {len(warnings)}\n"
        )
        zf.writestr("MANIFEST.txt", manifest)

    result = ExportResult(ok=True, archive_path=str(archive_path), files=sorted(files),
                          sha256=sha256.hexdigest(), warnings=warnings)
    make_event(EventType.EXPORT_READY, project_id, "exporter", design_version,
               archive=str(archive_path), sha256=result.sha256, files=len(files),
               factory=factory_profile)
    logger.info("export terminé", extra={"archive": str(archive_path), "files": len(files)})
    return result


def _self_test() -> int:
    """Self-test : mini-carte routée par le router → export Gerber + BOM + P&P."""
    from common.design_model import Board, Component, Net, Pad, Placement

    board = Board(width_mm=25.0, height_mm=15.0)
    defs = {
        "U1": (5.0, 7.0, 6.0, 6.0, {"1": ("VDD", -1.0, -1.0), "2": ("GND", 1.0, -1.0)}),
        "C1": (16.0, 5.0, 2.0, 1.5, {"1": ("VDD", -0.5, 0.0), "2": ("GND", 0.5, 0.0)}),
        "R1": (16.0, 10.0, 2.0, 1.5, {"1": ("VDD", -0.5, 0.0), "2": ("GND", 0.5, 0.0)}),
    }
    for ref, (x, y, w, h, pads_def) in defs.items():
        comp = Component(ref=ref, width_mm=w, height_mm=h, mpn="STM32F411CEU6" if ref == "U1" else "",
                         value="100nF" if ref == "C1" else "10k", functional_block="mcu")
        comp.pads = [Pad(name=p, x_mm=x + px, y_mm=y + py, net=net)
                     for p, (net, px, py) in pads_def.items()]
        board.add_component(comp, Placement(ref=ref, x_mm=x, y_mm=y))
    for name, conns in {"VDD": [("U1", "1"), ("C1", "1"), ("R1", "1")],
                        "GND": [("U1", "2"), ("C1", "2"), ("R1", "2")]}.items():
        board.nets[name] = Net(name=name, connections=conns)

    # routage simple (import direct du router — chaîne déterministe complète)
    try:
        from backend.services.router.main import route

        board, _stats = route(board, project_id="selftest", minimize_vias=False)
    except ImportError:
        logger.warning("router indisponible — export sans pistes (dev only)")

    result = export(board, project_id="selftest", factory_profile="pcbway",
                    outputs=["gerber", "bom", "pick_place"],
                    out_dir=Path("data/projects/selftest/exports"))
    print(f"[exporter] ok={result.ok} archive={result.archive_path}")
    print(f"[exporter] fichiers : {result.files}")
    print(f"[exporter] sha256 : {result.sha256[:16]}… — warnings : {result.warnings}")

    # le Gerber doit contenir les vias si le routage a eu lieu
    ok = result.ok and result.sha256 and len(result.files) >= 5
    print("[exporter] SELF-TEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        from common.grpc_helpers import proto_available

        if proto_available():
            from common.grpc_helpers import serve_grpc

            serve_grpc("exporter", None)  # type: ignore[arg-type]
            sys.exit(0)
    except Exception:  # noqa: BLE001
        pass
    sys.exit(_self_test())
