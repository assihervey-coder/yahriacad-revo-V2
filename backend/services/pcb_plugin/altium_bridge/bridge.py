"""Pont Altium Designer — synchronisation bidirectionnelle placements/routage.

Format d'échange : ASCII Altium **simplifié** (le binaire .PcbDoc propriétaire
est hors de portée d'un pont open-source — voir section 6.3). Une paire
`clé=valeur` par ligne, sections entre crochets :

    [PCB]
    boardW=40.00
    boardH=30.00
    layers=4
    [COMPONENT]
    ref=U1
    footprint=LQFP-64
    x=20.00
    locked=0
    [NET]
    name=+3V3
    class=Power
    impedance=50
    conn=U1/VDD;R1/1
    [TRACK]
    net=+3V3
    x1=5.00
    via=0
    [ZONE]
    name=keepout_usb
    kind=keepout
    maxtemp=

Toute ligne ou clé inconnue est JOURNALISÉE comme écart de sémantique puis
ignorée — l'import ne doit jamais échouer sur un fichier imparfait. `diff()`
mesure les écarts restants après un aller-retour (contrat : 0 écart sur un
design intégralement représentable par le format).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from common.design_model import Board, Component, Net, Placement, Segment, Zone
from common.log import get_logger

from backend.services.pcb_plugin.altium_bridge.mapping import (
    altium_class_to_internal,
    internal_class_to_altium,
    load_mapping,
)

logger = get_logger("pcb_plugin.altium_bridge.bridge")


@dataclass
class ImportReport:
    """Résultat d'import — carte + écarts de sémantique journalisés."""

    board: Board
    warnings: List[str] = field(default_factory=list)
    components: int = 0
    nets: int = 0
    segments: int = 0
    zones: int = 0


def _fmt(value: float) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


class AltiumBridge:
    """Pont bidirectionnel — un objet par projet (mapping partagé)."""

    def __init__(self, mapping: Optional[dict] = None) -> None:
        self.mapping = mapping or load_mapping()

    # ------------------------------------------------------------------ export
    def export_board(self, board: Board, out_path: Path) -> Path:
        """Sérialise la carte au format ASCII Altium simplifié (1 clé=valeur/ligne)."""
        lines: List[str] = [
            "[PCB]",
            f"boardW={_fmt(board.width_mm)}",
            f"boardH={_fmt(board.height_mm)}",
            f"layers={len(board.layers)}",
        ]
        for ref, comp in board.components.items():
            placement = board.placements.get(ref, Placement(ref=ref))
            lines += [
                "[COMPONENT]",
                f"ref={comp.ref}",
                f"footprint={(comp.footprint or comp.mpn or 'UNKNOWN').replace(' ', '_')}",
                f"value={comp.value.replace(' ', '_') if comp.value else ''}",
                f"w={_fmt(comp.width_mm)}",
                f"h={_fmt(comp.height_mm)}",
                f"pins={comp.pins}",
                f"power={_fmt(comp.power_w)}",
                f"functional={comp.functional_block or ''}",
                f"x={_fmt(placement.x_mm)}",
                f"y={_fmt(placement.y_mm)}",
                f"rot={_fmt(placement.rotation_deg)}",
                f"layer={placement.layer}",
                f"locked={1 if placement.locked else 0}",
            ]
        for net in board.nets.values():
            lines += [
                "[NET]",
                f"name={net.name}",
                f"class={internal_class_to_altium(net.net_class, self.mapping)}",
                f"impedance={_fmt(net.impedance_target_ohm) if net.impedance_target_ohm else '0'}",
                f"conn={';'.join(f'{ref}/{pad}' for ref, pad in net.connections)}",
            ]
            for seg in net.routed_segments:
                lines += [
                    "[TRACK]",
                    f"net={net.name}",
                    f"x1={_fmt(seg.x1_mm)}",
                    f"y1={_fmt(seg.y1_mm)}",
                    f"x2={_fmt(seg.x2_mm)}",
                    f"y2={_fmt(seg.y2_mm)}",
                    f"layer={seg.layer}",
                    f"w={_fmt(seg.width_mm)}",
                    f"via={1 if seg.is_via else 0}",
                ]
        for zone in board.zones:
            lines += [
                "[ZONE]",
                f"name={zone.name.replace(' ', '_')}",
                f"kind={zone.kind}",
                f"x0={_fmt(zone.x_min_mm)}",
                f"y0={_fmt(zone.y_min_mm)}",
                f"x1={_fmt(zone.x_max_mm)}",
                f"y1={_fmt(zone.y_max_mm)}",
                f"maxtemp={_fmt(zone.max_temp_c) if zone.max_temp_c is not None else ''}",
            ]
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("export Altium écrit", extra={"path": str(out_path),
                                                  "components": len(board.components)})
        return out_path

    # ------------------------------------------------------------------ import
    def import_pcbdoc(self, path: Path) -> ImportReport:
        """Parse un fichier ASCII Altium simplifié → carte interne + écarts journalisés."""
        warnings: List[str] = []
        board = Board()
        state = "none"
        comp: Optional[Component] = None
        placement: Optional[Placement] = None
        net: Optional[Net] = None
        seg: Optional[Segment] = None
        zone: Optional[Dict[str, str]] = None

        def finalize_component() -> None:
            nonlocal comp, placement
            if comp is not None and comp.ref:
                board.add_component(comp, placement or Placement(ref=comp.ref))
            elif comp is not None:
                warnings.append("composant sans ref ignoré")
            comp, placement = None, None

        def finalize_segment() -> None:
            nonlocal seg
            if seg is not None and net is not None:
                net.routed_segments.append(seg)
            seg = None

        def finalize_zone() -> None:
            nonlocal zone
            if zone:
                try:
                    board.zones.append(Zone(
                        name=zone.get("name", "zone"),
                        kind=zone.get("kind", "keepout") or "keepout",
                        x_min_mm=_float(zone.get("x0"), 0.0),
                        y_min_mm=_float(zone.get("y0"), 0.0),
                        x_max_mm=_float(zone.get("x1"), 0.0),
                        y_max_mm=_float(zone.get("y1"), 0.0),
                        max_temp_c=(_float(zone["maxtemp"], 0.0)
                                    if zone.get("maxtemp") else None),
                    ))
                except Exception as exc:  # zone malformée → écart journalisé
                    warnings.append(f"zone ignorée ({exc})")
                    logger.info("écart de sémantique : zone malformée", extra={"error": str(exc)})
            zone = None

        for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].upper()
                finalize_component()
                finalize_segment()
                finalize_zone()
                if section == "COMPONENT":
                    comp, placement = Component(ref=""), None
                    state = "component"
                elif section == "NET":
                    net = None
                    state = "net"
                elif section == "TRACK":
                    state = "track"
                elif section == "ZONE":
                    zone = {}
                    state = "zone"
                else:
                    state = "none"
                    if section != "PCB":
                        warnings.append(f"section inconnue [{section}] ignorée")
                        logger.info("écart de sémantique : section inconnue",
                                    extra={"section": section})
                continue

            kv = _parse_kv(line)
            if kv is None:
                warnings.append(f"ligne non structurée ignorée : {line[:40]!r}")
                logger.info("écart de sémantique : ligne illisible ignorée",
                            extra={"line": line[:40]})
                continue
            key, value = kv

            if state == "pcb" or state == "none":
                if key == "boardW":
                    board.width_mm = _float(value, 100.0)
                elif key == "boardH":
                    board.height_mm = _float(value, 80.0)
                elif key == "layers" and int(_float(value, 4)) != len(board.layers):
                    warnings.append(
                        f"nb de couches Altium ({value}) ≠ modèle ({len(board.layers)}) — pile conservée")
            elif state == "component" and comp is not None:
                comp, placement = _feed_component(comp, placement, key, value, warnings)
            elif state == "net":
                if key == "name":
                    net = board.nets.get(value) or Net(name=value)
                    board.nets[value] = net
                elif net is None:
                    warnings.append(f"clé NET '{key}' avant name — ignorée")
                elif key == "class":
                    net.net_class = altium_class_to_internal(value, self.mapping)
                elif key == "impedance":
                    net.impedance_target_ohm = _float(value, 0.0) or None
                elif key == "conn":
                    for item in value.split(";"):
                        if "/" in item:
                            ref, pad = item.split("/", 1)
                            net.connections.append((ref, pad))
                        elif item:
                            warnings.append(f"connexion malformée '{item}' ignorée")
                else:
                    warnings.append(f"clé NET inconnue '{key}' ignorée")
            elif state == "track":
                if key == "net":
                    finalize_segment()
                    net = board.nets.get(value) or board.nets.setdefault(value, Net(name=value))
                elif seg is None and net is not None:
                    seg = Segment(net=net.name, x1_mm=0, y1_mm=0, x2_mm=0, y2_mm=0, layer=0)
                if seg is not None:
                    if key == "x1":
                        seg.x1_mm = _float(value, 0.0)
                    elif key == "y1":
                        seg.y1_mm = _float(value, 0.0)
                    elif key == "x2":
                        seg.x2_mm = _float(value, 0.0)
                    elif key == "y2":
                        seg.y2_mm = _float(value, 0.0)
                    elif key == "layer":
                        seg.layer = int(_float(value, 0))
                    elif key == "w":
                        seg.width_mm = _float(value, 0.2)
                    elif key == "via":
                        seg.is_via = value.strip() in ("1", "true", "True")
                        finalize_segment()
                    else:
                        warnings.append(f"clé TRACK inconnue '{key}' ignorée")
            elif state == "zone":
                zone = zone or {}
                zone[key] = value

        finalize_component()
        finalize_segment()
        finalize_zone()

        segments_total = sum(len(n.routed_segments) for n in board.nets.values())
        report = ImportReport(
            board=board, warnings=warnings,
            components=len(board.components), nets=len(board.nets),
            segments=segments_total, zones=len(board.zones),
        )
        if warnings:
            logger.warning("import Altium avec écarts de sémantique",
                           extra={"warnings": len(warnings), "path": str(path)})
        return report

    # ------------------------------------------------------------------ diff
    def diff(self, board_a: Board, board_b: Board) -> List[str]:
        """Écarts de sémantique entre deux cartes (après aller-retour attendu : vide)."""
        gaps: List[str] = []
        refs_a, refs_b = set(board_a.components), set(board_b.components)
        for ref in sorted(refs_a - refs_b):
            gaps.append(f"composant absent en B : {ref}")
        for ref in sorted(refs_b - refs_a):
            gaps.append(f"composant absent en A : {ref}")
        for ref in sorted(refs_a & refs_b):
            pa, pb = board_a.placements[ref], board_b.placements[ref]
            if abs(pa.x_mm - pb.x_mm) > 1e-3 or abs(pa.y_mm - pb.y_mm) > 1e-3:
                gaps.append(f"placement {ref} : ({pa.x_mm},{pa.y_mm}) ≠ ({pb.x_mm},{pb.y_mm})")
            if abs(pa.rotation_deg - pb.rotation_deg) > 1e-3:
                gaps.append(f"rotation {ref} : {pa.rotation_deg} ≠ {pb.rotation_deg}")
        names_a, names_b = set(board_a.nets), set(board_b.nets)
        for name in sorted(names_a - names_b):
            gaps.append(f"net absent en B : {name}")
        for name in sorted(names_b - names_a):
            gaps.append(f"net absent en A : {name}")
        for name in sorted(names_a & names_b):
            la = board_a.nets[name].routed_length_mm
            lb = board_b.nets[name].routed_length_mm
            if abs(la - lb) > 1e-3:
                gaps.append(f"longueur routée {name} : {la:.2f} ≠ {lb:.2f} mm")
        return gaps


# --------------------------------------------------------------------------- #
#  Helpers de parsing (tolérants — jamais d'exception sur un fichier imparfait)
# --------------------------------------------------------------------------- #
def _parse_kv(line: str) -> Optional[Tuple[str, str]]:
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip()


def _float(value: Optional[str], default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _feed_component(comp: Component, placement: Optional[Placement],
                    key: str, value: str, warnings: List[str]):
    """Injecte une clé [COMPONENT] dans l'objet courant (placement créé au premier x)."""
    if key == "ref":
        comp.ref = value
        placement = placement or Placement(ref=value)
    elif key == "footprint":
        comp.footprint = value  # encodé sans espace à l'export (' ' → '_')
    elif key == "value":
        comp.value = value
    elif key == "w":
        comp.width_mm = _float(value, 5.0)
    elif key == "h":
        comp.height_mm = _float(value, 5.0)
    elif key == "pins":
        comp.pins = int(_float(value, 0))
    elif key == "power":
        comp.power_w = _float(value, 0.0)
    elif key == "functional":
        comp.functional_block = value
    elif key == "x" and placement is not None:
        placement.x_mm = _float(value, 0.0)
    elif key == "y" and placement is not None:
        placement.y_mm = _float(value, 0.0)
    elif key == "rot" and placement is not None:
        placement.rotation_deg = _float(value, 0.0)
    elif key == "layer" and placement is not None:
        placement.layer = int(_float(value, 0))
    elif key == "locked" and placement is not None:
        placement.locked = value.strip() in ("1", "true", "True")
    else:
        warnings.append(f"clé COMPONENT inconnue '{key}' ignorée")
        logger.info("écart de sémantique : clé COMPONENT ignorée", extra={"key": key})
    return comp, placement
