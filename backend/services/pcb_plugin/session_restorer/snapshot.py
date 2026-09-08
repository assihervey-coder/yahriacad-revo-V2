"""Persistance du contexte complet de session — snapshot JSON versionné + checksum.

Un snapshot capture TOUT ce qu'il faut pour reprendre exactement un travail
interrompu (section 6.3) :
  - design : carte sérialisée (composants, placements, nets, segments, zones) ;
  - position du moteur : dernier segment routé, net en cours, étape du routeur ;
  - événements en vol : événements émis non encore consommés par le frontend ;
  - résidus de calcul : résultats partiels (thermique/SI) réutilisables.

Format : JSON avec `schema_version`, checksum SHA-256 du contenu canonique —
toute altération du fichier est détectée au chargement (SnapshotIntegrityError).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.design_model import Board, Component, Net, Pad, Placement, Segment, Zone
from common.log import get_logger

logger = get_logger("pcb_plugin.session_restorer")

SNAPSHOT_SCHEMA_VERSION = 2
SNAPSHOT_PREFIX = "session__"


class SnapshotIntegrityError(Exception):
    """Le snapshot est corrompu (checksum invalide) — refus de rejouer."""


# --------------------------------------------------------------------------- #
#  Sérialisation du modèle design (local au service — évite d'étendre common)
# --------------------------------------------------------------------------- #
def board_to_dict(board: Board) -> Dict[str, Any]:
    """Board → dict JSON-sérialisable (perte zéro sur les champs du modèle commun)."""
    return {
        "width_mm": board.width_mm,
        "height_mm": board.height_mm,
        "layers": [{"index": l.index, "name": l.name,
                    "thickness_um": l.thickness_um, "is_copper": l.is_copper}
                   for l in board.layers],
        "components": [
            {
                "ref": c.ref, "mpn": c.mpn, "value": c.value, "footprint": c.footprint,
                "pins": c.pins, "width_mm": c.width_mm, "height_mm": c.height_mm,
                "power_w": c.power_w, "price_usd": c.price_usd, "stock": c.stock,
                "functional_block": c.functional_block,
                "pads": [{"name": p.name, "x_mm": p.x_mm, "y_mm": p.y_mm,
                          "diameter_mm": p.diameter_mm, "net": p.net}
                         for p in c.pads],
            }
            for c in board.components.values()
        ],
        "placements": [
            {"ref": p.ref, "x_mm": p.x_mm, "y_mm": p.y_mm,
             "rotation_deg": p.rotation_deg, "layer": p.layer, "locked": p.locked}
            for p in board.placements.values()
        ],
        "nets": [
            {
                "name": n.name,
                "connections": [list(c) for c in n.connections],
                "net_class": n.net_class,
                "impedance_target_ohm": n.impedance_target_ohm,
                "length_match_group": n.length_match_group,
                "routed_segments": [
                    {"net": s.net, "x1_mm": s.x1_mm, "y1_mm": s.y1_mm,
                     "x2_mm": s.x2_mm, "y2_mm": s.y2_mm, "layer": s.layer,
                     "width_mm": s.width_mm, "is_via": s.is_via}
                    for s in n.routed_segments
                ],
                "layer_allowlist": list(n.layer_allowlist),
            }
            for n in board.nets.values()
        ],
        "zones": [
            {"name": z.name, "x_min_mm": z.x_min_mm, "y_min_mm": z.y_min_mm,
             "x_max_mm": z.x_max_mm, "y_max_mm": z.y_max_mm, "kind": z.kind,
             "max_temp_c": z.max_temp_c}
            for z in board.zones
        ],
    }


def board_from_dict(data: Dict[str, Any]) -> Board:
    """Reconstruit une Board depuis le dict produit par `board_to_dict`."""
    from common.design_model import Layer

    board = Board(width_mm=data.get("width_mm", 100.0), height_mm=data.get("height_mm", 80.0))
    if data.get("layers"):
        board.layers = [Layer(index=l["index"], name=l["name"],
                              thickness_um=l.get("thickness_um", 35.0),
                              is_copper=l.get("is_copper", True)) for l in data["layers"]]
    for comp in data.get("components", []):
        pads = [Pad(name=p["name"], x_mm=p["x_mm"], y_mm=p["y_mm"],
                    diameter_mm=p.get("diameter_mm", 0.6), net=p.get("net"))
                for p in comp.get("pads", [])]
        board.components[comp["ref"]] = Component(
            ref=comp["ref"], mpn=comp.get("mpn", ""), value=comp.get("value", ""),
            footprint=comp.get("footprint", ""), pins=comp.get("pins", 0),
            width_mm=comp.get("width_mm", 5.0), height_mm=comp.get("height_mm", 5.0),
            power_w=comp.get("power_w", 0.0), price_usd=comp.get("price_usd", 0.0),
            stock=comp.get("stock", 0), functional_block=comp.get("functional_block", ""),
            pads=pads,
        )
    for pl in data.get("placements", []):
        board.placements[pl["ref"]] = Placement(
            ref=pl["ref"], x_mm=pl.get("x_mm", 0.0), y_mm=pl.get("y_mm", 0.0),
            rotation_deg=pl.get("rotation_deg", 0.0), layer=pl.get("layer", 0),
            locked=pl.get("locked", False),
        )
    for net in data.get("nets", []):
        segments = [Segment(net=s["net"], x1_mm=s["x1_mm"], y1_mm=s["y1_mm"],
                            x2_mm=s["x2_mm"], y2_mm=s["y2_mm"], layer=s["layer"],
                            width_mm=s.get("width_mm", 0.2), is_via=s.get("is_via", False))
                    for s in net.get("routed_segments", [])]
        board.nets[net["name"]] = Net(
            name=net["name"],
            connections=[tuple(c) for c in net.get("connections", [])],
            net_class=net.get("net_class", "default"),
            impedance_target_ohm=net.get("impedance_target_ohm"),
            length_match_group=net.get("length_match_group"),
            routed_segments=segments,
            layer_allowlist=net.get("layer_allowlist", list(range(8))),
        )
    board.zones = [Zone(name=z["name"], x_min_mm=z["x_min_mm"], y_min_mm=z["y_min_mm"],
                        x_max_mm=z["x_max_mm"], y_max_mm=z["y_max_mm"], kind=z.get("kind", "keepout"),
                        max_temp_c=z.get("max_temp_c"))
                   for z in data.get("zones", [])]
    return board


# --------------------------------------------------------------------------- #
#  Snapshot
# --------------------------------------------------------------------------- #
@dataclass
class SessionSnapshot:
    """Contexte complet d'une session interrompue — rejouable par SessionRestorer."""

    schema_version: int
    project_id: str
    session_id: str
    created_at: float
    channel: str                       # browser | kicad | altium — cible de reprise
    design: Dict[str, Any]             # board_to_dict()
    design_version: int
    engine_position: Dict[str, Any]    # last_segment, current_net, router_step, progress_pct
    inflight_events: List[Dict[str, Any]]  # Event.to_json() non consommés, triés par seq
    compute_residue: Dict[str, Any]    # résultats partiels réutilisables
    checksum: str = ""

    def canonical_payload(self) -> Dict[str, Any]:
        """Charge utile checksumée (tout sauf le checksum lui-même)."""
        return {k: v for k, v in asdict(self).items() if k != "checksum"}

    def to_json(self) -> Dict[str, Any]:
        payload = self.canonical_payload()
        payload["checksum"] = self.checksum
        return payload

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "SessionSnapshot":
        return cls(
            schema_version=raw.get("schema_version", 1),
            project_id=raw.get("project_id", ""),
            session_id=raw.get("session_id", ""),
            created_at=raw.get("created_at", 0.0),
            channel=raw.get("channel", "browser"),
            design=raw.get("design", {}),
            design_version=raw.get("design_version", 1),
            engine_position=raw.get("engine_position", {}),
            inflight_events=raw.get("inflight_events", []),
            compute_residue=raw.get("compute_residue", {}),
            checksum=raw.get("checksum", ""),
        )


def compute_checksum(payload: Dict[str, Any]) -> str:
    """SHA-256 du JSON canonique (clés triées) — détecte toute altération."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def capture(
    project_id: str,
    session_id: str,
    board: Board,
    design_version: int = 1,
    engine_position: Optional[Dict[str, Any]] = None,
    inflight_events: Optional[List[Dict[str, Any]]] = None,
    compute_residue: Optional[Dict[str, Any]] = None,
    channel: str = "browser",
) -> SessionSnapshot:
    """Capture un snapshot cohérent — checksum calculé sur le contenu canonique."""
    snapshot = SessionSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        project_id=project_id,
        session_id=session_id,
        created_at=time.time(),
        channel=channel,
        design=board_to_dict(board),
        design_version=design_version,
        engine_position=engine_position or {},
        inflight_events=sorted(inflight_events or [], key=lambda e: e.get("seq", 0)),
        compute_residue=compute_residue or {},
    )
    snapshot.checksum = compute_checksum(snapshot.canonical_payload())
    logger.info("snapshot de session capturé",
                extra={"project_id": project_id, "session_id": session_id,
                       "events": len(snapshot.inflight_events)})
    return snapshot


def snapshot_path(directory: Path, project_id: str, session_id: str) -> Path:
    """Chemin du fichier snapshot (identifiants assainis)."""
    safe = lambda text: "".join(c if c.isalnum() or c in "-_" else "_" for c in text)  # noqa: E731
    return Path(directory) / f"{SNAPSHOT_PREFIX}{safe(project_id)}__{safe(session_id)}.json"


def save(snapshot: SessionSnapshot, directory: Path) -> Path:
    """Écrit le snapshot (atomique : fichier temporaire + rename)."""
    path = snapshot_path(directory, snapshot.project_id, snapshot.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot.to_json(), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)
    logger.info("snapshot persisté", extra={"path": str(path)})
    return path


def load(path: Path) -> SessionSnapshot:
    """Charge un snapshot en vérifiant l'intégrité — SnapshotIntegrityError si corrompu."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    snapshot = SessionSnapshot.from_json(raw)
    expected = compute_checksum(snapshot.canonical_payload())
    if snapshot.checksum != expected:
        raise SnapshotIntegrityError(
            f"checksum invalide : {snapshot.checksum[:12]}… ≠ {expected[:12]}…")
    if snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION:
        logger.warning("version de snapshot différente — reprise best-effort",
                       extra={"found": snapshot.schema_version,
                              "expected": SNAPSHOT_SCHEMA_VERSION})
    return snapshot
