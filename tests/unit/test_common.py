"""Tests unitaires du socle commun : design_model, bus, credits, events.

Cible : la colonne vertébrale partagée par les 9 services. Un defect ici se
propage partout — ces tests doivent rester rapides (aucun réseau, aucun
fichier hors tmp_path).
"""

from __future__ import annotations

import pytest

from common.bus import ConstraintKind, ConstraintMessage, InMemoryConstraintBus
from common.credits import CreditLedger, InsufficientCredits, Pricing
from common.design_model import Board, Component, Net, Placement, Segment
from common.events import Event, EventType, make_event


# ---------------------------------------------------------------------------
# design_model — scoring, move, overlap
# ---------------------------------------------------------------------------

def _wire(board: Board, net_name: str, refs: list[str]) -> Net:
    """Net câblé : segments en étoile depuis le premier composant + 1 via."""
    net = Net(name=net_name, connections=[(ref, "1") for ref in refs])
    first = board.placements[refs[0]]
    for ref in refs[1:]:
        other = board.placements[ref]
        net.routed_segments.append(
            Segment(net=net_name, x1_mm=first.x_mm, y1_mm=first.y_mm,
                    x2_mm=other.x_mm, y2_mm=other.y_mm, layer=0)
        )
    net.routed_segments.append(
        Segment(net=net_name, x1_mm=first.x_mm, y1_mm=first.y_mm,
                x2_mm=first.x_mm + 1.0, y2_mm=first.y_mm, layer=1, is_via=True)
    )
    return net


def _board_two_components() -> Board:
    board = Board(width_mm=40.0, height_mm=30.0)
    board.add_component(Component(ref="U1", mpn="STM32F411CEU6", width_mm=7.0, height_mm=7.0),
                        Placement(ref="U1", x_mm=10.0, y_mm=15.0))
    board.add_component(Component(ref="R1", mpn="RC0402FR-0710KL", width_mm=1.0, height_mm=0.5),
                        Placement(ref="R1", x_mm=25.0, y_mm=15.0))
    return board


def test_board_scoring_full_routing_is_100() -> None:
    """Tous les nets routés, une poignée de vias -> score quasi parfait.

    Formule contractuelle de Board.drc_score() : 100 x complétude x
    (1 - via_count/2000) — ici 1 via -> 100 x 1 x (1 - 0,0005) = 99,95.
    """
    board = _board_two_components()
    board.nets["SWD"] = _wire(board, "SWD", ["U1", "R1"])
    assert board.unrouted_nets() == []
    assert board.drc_score() == pytest.approx(99.95, abs=1e-6)


def test_board_scoring_penalizes_unrouted_and_vias() -> None:
    """Un net non routé abaisse le score ; les vias ajoutent une pénalité."""
    board = _board_two_components()
    board.nets["SWD"] = _wire(board, "SWD", ["U1", "R1"])
    board.nets["VDD"] = Net(name="VDD")  # non routé
    score_unrouted = board.drc_score()
    assert 0.0 < score_unrouted < 100.0

    # 300 vias -> pénalité plafonnée à 20 % (via_count/2000, max 0.2).
    for _ in range(300):
        board.nets["SWD"].routed_segments.append(
            Segment(net="SWD", x1_mm=0, y1_mm=0, x2_mm=0.1, y2_mm=0, layer=0, is_via=True))
    assert board.drc_score() < score_unrouted


def test_board_move_updates_position_and_rejects_locked() -> None:
    """move() déplace atomiquement, refuse l'inconnu et le verrouillé."""
    board = _board_two_components()
    moved = board.move("R1", 30.0, 20.0, rotation_deg=90.0)
    assert (moved.x_mm, moved.y_mm, moved.rotation_deg) == (30.0, 20.0, 90.0)
    assert board.placements["R1"].y_mm == 20.0

    board.placements["R1"].locked = True
    with pytest.raises(PermissionError):
        board.move("R1", 5.0, 5.0)
    with pytest.raises(KeyError):
        board.move("X99", 1.0, 1.0)


def test_board_overlap_detection() -> None:
    """Chevauchement d'empreintes : vrai au même endroit, faux à distance."""
    board = _board_two_components()
    # R1 (0,5 mm) déposé pile sur U1 (7 mm) -> chevauchement.
    board.move("R1", 10.0, 15.0)
    assert board.bounding_box_overlap("U1", "R1") is True
    # À 25 mm de distance -> séparé (U1 s'étend jusqu'à x=13.5).
    board.move("R1", 25.0, 15.0)
    assert board.bounding_box_overlap("U1", "R1") is False


# ---------------------------------------------------------------------------
# bus — publish / subscribe / latest < 50 ms
# ---------------------------------------------------------------------------

def test_bus_publish_subscribe_latest_and_latency() -> None:
    """Le bus retient la dernière valeur par clé, filtre par kind et reste
    sous la cible de 50 ms (spécification bus de contraintes)."""
    bus = InMemoryConstraintBus(target_latency_ms=50.0)
    received: list[ConstraintMessage] = []
    token_all = bus.subscribe(None, received.append)
    token_thermal = bus.subscribe([ConstraintKind.THERMAL_ZONE_UPDATE], received.append)

    for temp in (42.0, 55.0, 61.0):
        latency_ms = bus.publish(ConstraintMessage(
            kind=ConstraintKind.THERMAL_ZONE_UPDATE,
            key="thermal/U12",
            value={"temp_c": temp},
            source="multi_physics_loop",
        ))
        assert latency_ms < 50.0

    # 3 publications, 3 appels du subscriber global + 3 du thermal-only.
    assert len(received) == 6
    # Dernière valeur retenue par clé :
    latest = bus.latest(ConstraintKind.THERMAL_ZONE_UPDATE)
    assert latest["thermal/U12"].value["temp_c"] == 61.0
    assert latest["thermal/U12"].revision == 3

    bus.unsubscribe(token_all)
    bus.unsubscribe(token_thermal)
    received.clear()
    bus.publish(ConstraintMessage(kind=ConstraintKind.KEEPOUT_ZONE, key="keepout/ANT",
                                  value={"x_min": 0, "y_min": 0}))
    assert received == []  # désinscrit -> plus rien ne parvient


# ---------------------------------------------------------------------------
# credits — debit, solde insuffisant, estimate
# ---------------------------------------------------------------------------

def _ledger(tmp_path) -> CreditLedger:
    """Grand livre isolé (tmp_path) — ne touche jamais data/projects."""
    return CreditLedger(balance_usd=10.0, ledger_path=tmp_path / "credits.json")


def test_credits_debit_updates_balance_and_history(tmp_path) -> None:
    """debit() décrémente le solde, journalise et persiste le grand livre."""
    ledger = _ledger(tmp_path)
    tx = ledger.debit("proj-1", Pricing.ROUTING_PASS, user="router")
    assert ledger.balance_usd == pytest.approx(9.60, abs=1e-4)
    assert tx.amount_usd == pytest.approx(0.40, abs=1e-4)
    assert tx.balance_after_usd == pytest.approx(9.60, abs=1e-4)
    assert [t.tx_id for t in ledger.history("proj-1")] == [tx.tx_id]
    # Quantités : 3 fast_eval (0,004 USD x 3).
    ledger.debit("proj-1", Pricing.FAST_EVAL_ITERATION, quantity=3)
    assert ledger.balance_usd == pytest.approx(9.588, abs=1e-4)
    # Persistance best-effort sur disque (tmp_path) :
    assert (tmp_path / "credits.json").exists()


def test_credits_insufficient_balance_raises(tmp_path) -> None:
    """Une action au-delà du solde lève InsufficientCredits — aucun débit."""
    # Solde 1,00 USD, nuit d'optimisation à 1,20 USD -> refus net.
    ledger = CreditLedger(balance_usd=1.0, ledger_path=tmp_path / "broke.json")
    with pytest.raises(InsufficientCredits):
        ledger.debit("proj-2", Pricing.NIGHT_OPTIMIZATION)
    assert ledger.balance_usd == 1.0
    assert ledger.history() == []


def test_credits_estimate(tmp_path) -> None:
    """estimate() donne le coût projeté et l'accessibilité sans débit."""
    ledger = _ledger(tmp_path)
    est = ledger.estimate(Pricing.FAST_EVAL_ITERATION, quantity=300)
    assert est["estimated_usd"] == pytest.approx(1.20, abs=1e-4)
    assert est["affordable"] is True
    assert est["balance_usd"] == pytest.approx(10.0, abs=1e-4)
    assert ledger.balance_usd == 10.0  # estimation ne débite pas


# ---------------------------------------------------------------------------
# events — make_event + round-trip JSON
# ---------------------------------------------------------------------------

def test_event_make_and_json_roundtrip() -> None:
    """make_event + to_json/from_json conservent toute la charge utile."""
    event = make_event(EventType.NET_ROUTED, "proj-42", emitter="router",
                       design_version=7, net_id="ANT_MATCH", vias=2)
    assert event.type == EventType.NET_ROUTED
    assert event.payload == {"net_id": "ANT_MATCH", "vias": 2}

    raw = event.to_json()
    clone = Event.from_json(raw)
    assert clone.to_json() == raw          # round-trip stable
    assert clone.event_id == event.event_id
    assert clone.seq == event.seq
    assert clone.design_version == 7
    assert clone.type == EventType.NET_ROUTED

    # La nomenclature accepte aussi les chaînes (contrat frontend/plugins) :
    assert make_event("credit_debit", "proj-42").type == EventType.CREDIT_DEBIT
