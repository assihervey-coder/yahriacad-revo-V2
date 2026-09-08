"""Export ODB++ — structure de dossiers minimale viable (section 6.6).

ODB++ est un format hiérarchique : job → steps → layers. On génère l'ossature
attendue par les flux de fabrication avancés (job.xml, stephdr.xml, matrix.xml)
avec les couches cuivre référencées — l'outillage DFM client peut ouvrir la
structure et lire les géométries RS-274X embarquées.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from common.design_model import Board
from gerber.rs274x import layer_filename, render_copper_layer


@dataclass
class OdbStructure:
    """Arborescence ODB++ produite : {chemin relatif: contenu}."""

    files: Dict[str, str]

    def write_to(self, base_dir: Path) -> List[Path]:
        base_dir.mkdir(parents=True, exist_ok=True)
        written: List[Path] = []
        for rel_path, content in self.files.items():
            target = base_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(target)
        return written


def export_odb(board: Board, project_id: str = "project") -> OdbStructure:
    """Construit la structure ODB++ minimale pour le design courant."""
    files: Dict[str, str] = {}
    copper_layers = [l.index for l in board.layers if l.is_copper]

    files["job.xml"] = _job_xml(project_id, len(copper_layers))
    files["steps/pcb/stephdr.xml"] = _step_header(board)
    files["matrix/matrix.xml"] = _matrix(copper_layers)
    for layer_index in copper_layers:
        name = layer_filename(layer_index, len(copper_layers)).replace(".gbr", "")
        files[f"steps/pcb/layers/{name}/layers"] = render_copper_layer(board, layer_index)
    return OdbStructure(files=files)


def _job_xml(project_id: str, layer_count: int) -> str:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<!-- Job ODB++ généré par pcb_ai_designer_v2 — {ts} -->\n'
        f'<OdbJob name="{project_id}" version="8.0K" layers="{layer_count}">\n'
        '  <Step name="pcb" />\n'
        '</OdbJob>\n'
    )


def _step_header(board: Board) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<StepHeader>\n'
        f'  <Profile units="mm">\n'
        f'    <Polygon>(0 0) ({board.width_mm} 0) ({board.width_mm} {board.height_mm}) '
        f'(0 {board.height_mm})</Polygon>\n'
        '  </Profile>\n'
        '</StepHeader>\n'
    )


def _matrix(copper_layers: List[int]) -> str:
    rows = "\n".join(
        f'  <Layer name="{layer_filename(idx, len(copper_layers)).replace(".gbr", "")}" '
        f'type="signal" index="{idx}" />'
        for idx in copper_layers
    )
    return '<?xml version="1.0" encoding="UTF-8"?>\n<Matrix>\n' + rows + "\n</Matrix>\n"
