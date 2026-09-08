"""Calcul de deltas entre deux Board — reprise WebSocket par delta.

Produit un payload MINIMAL (nets ajoutés/supprimés, composants déplacés,
segments modifiés) consommé par le viewer frontend après une déconnexion
(paramètre `?since_seq=`), et fabrique les événements component_moved /
net_routed correspondants (nomenclature section 11).
"""

from __future__ import annotations

from typing import Any, Iterable

from common.design_model import Board
from common.events import Event, EventType, make_event


def _place_snapshot(board: Board, ref: str) -> dict[str, Any]:
    p = board.placements[ref]
    return {"x_mm": p.x_mm, "y_mm": p.y_mm, "rotation_deg": p.rotation_deg, "layer": p.layer}


def compute_board_delta(before: Board, after: Board) -> dict[str, Any]:
    """Diff structurelle minimale entre deux états de carte."""
    before_refs = set(before.placements)
    after_refs = set(after.placements)
    moved = []
    for ref in sorted(before_refs & after_refs):
        if _place_snapshot(before, ref) != _place_snapshot(after, ref):
            moved.append({"ref": ref, "from": _place_snapshot(before, ref),
                          "to": _place_snapshot(after, ref)})
    nets_before = set(before.nets)
    nets_after = set(after.nets)
    segments_changed = []
    for name in sorted(nets_before & nets_after):
        sb = before.nets[name].routed_segments
        sa = after.nets[name].routed_segments
        if [(s.x1_mm, s.y1_mm, s.x2_mm, s.y2_mm, s.layer) for s in sb] != \
           [(s.x1_mm, s.y1_mm, s.x2_mm, s.y2_mm, s.layer) for s in sa]:
            segments_changed.append(name)
    return {
        "components_added": sorted(after_refs - before_refs),
        "components_removed": sorted(before_refs - after_refs),
        "components_moved": moved,
        "nets_added": sorted(nets_after - nets_before),
        "nets_removed": sorted(nets_before - nets_after),
        "nets_rerouted": segments_changed,
        "summary": (f"{len(moved)} déplacement(s), {len(segments_changed)} net(s) re-routé(s), "
                    f"{len(after_refs - before_refs)} ajout(s), "
                    f"{len(before_refs - after_refs)} suppression(s)"),
    }


def events_from_delta(delta: dict[str, Any], project_id: str,
                      design_version: int, emitter: str = "state_manager") -> list[Event]:
    """Traduit un delta en événements WebSocket (component_moved, net_routed)."""
    events: list[Event] = []
    for item in delta.get("components_moved", []):
        events.append(make_event(EventType.COMPONENT_MOVED, project_id, emitter,
                                 design_version=design_version, ref=item["ref"],
                                 **item["to"]))
    for name in delta.get("nets_rerouted", []) + delta.get("nets_added", []):
        events.append(make_event(EventType.NET_ROUTED, project_id, emitter,
                                 design_version=design_version, net=name))
    return events


def events_since(events: Iterable[Event], since_seq: int,
                 project_id: str | None = None) -> list[Event]:
    """Filtre un flux d'événements pour la reprise par delta (seq > since_seq)."""
    selected = [e for e in events if e.seq > since_seq]
    if project_id:
        selected = [e for e in selected if e.project_id == project_id]
    return sorted(selected, key=lambda e: e.seq)
