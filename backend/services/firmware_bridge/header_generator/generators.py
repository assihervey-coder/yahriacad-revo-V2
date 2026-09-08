"""Génération des en-têtes C — dispatch par cible Zephyr / Arduino (section 6.5).

Chaque générateur cible retourne (filename, contenu C) ; ce module les combine
avec les assignations sources dans des GeneratedHeader prêts à afficher dans le
firmware_preview (coloration syntaxique, diffs entre versions).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

_ROOT = Path(__file__).resolve().parents[4]           # racine du dépôt (common/)
_SERVICE_DIR = Path(__file__).resolve().parents[1]    # firmware_bridge/ (paquets frères)
for _path in (str(_ROOT), str(_SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from pin_exporter.exporter import PinAssignment, export_pins  # noqa: E402
from .zephyr import generate_zephyr_header  # noqa: E402
from .arduino import generate_arduino_header  # noqa: E402


@dataclass
class GeneratedHeader:
    """Un fichier .h généré : nom, contenu C, assignations sources."""

    filename: str
    c_source: str
    assignments: List[PinAssignment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"filename": self.filename, "c_source": self.c_source,
                "assignments": [a.to_dict() for a in self.assignments]}

    def __repr__(self) -> str:  # pragma: no cover - confort
        return f"GeneratedHeader({self.filename!r}, {len(self.assignments)} pins)"


def generate_headers(board, target: str = "zephyr") -> List[GeneratedHeader]:
    """Génère les en-têtes C synchronisés avec le design.

    target : "zephyr" → pin_map_zephyr.h (style devicetree commenté),
             "arduino" → pin_map_arduino.h (#define simples),
             "both" → les deux fichiers.
    """
    assignments = export_pins(board)
    headers: List[GeneratedHeader] = []
    targets = ["zephyr", "arduino"] if target == "both" else [target or "zephyr"]
    for tgt in targets:
        if tgt == "zephyr":
            filename, source = generate_zephyr_header(assignments)
        elif tgt == "arduino":
            filename, source = generate_arduino_header(assignments)
        else:
            raise ValueError(f"cible firmware inconnue : {tgt} (zephyr|arduino|both)")
        headers.append(GeneratedHeader(filename=filename, c_source=source,
                                       assignments=assignments))
    return headers
