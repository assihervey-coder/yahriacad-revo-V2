"""Chaîne déterministe bout-en-bout hors ligne — parse → placement → DRC → export.

La promesse Circuitron (« même entrée -> même sortie ») se vérifie ici SANS
aucun service externe : ce test embarque une mini-chaîne de référence (parser
SPICE minimal, placement manuel, DRC au sol, export Gerber via-flashed) bâtie
sur le modèle de design commun (`common.design_model`).

Les modules `backend.services.*` sont développés en parallèle : les tests qui
les ciblent sont gardés par `pytest.importorskip` / `skipif` — ils s'exécutent
dès que les couches sont fusionnées, et le harnais de benchmark
(tests/vs_quilter_benchmark) fait de même pour le moteur interne.
"""

from __future__ import annotations

import pytest

from common.design_model import Board, Component, Net, Placement, Segment

# ---------------------------------------------------------------------------
# Mini-chaîne de référence (déterministe, hors ligne)
# ---------------------------------------------------------------------------

BOARD_W_MM = 40.0
BOARD_H_MM = 30.0

# Netliste SPICE miniature : 1 MCU, 2 résistances, 1 LED, 1 condensateur.
MINI_NETLIST = """\
* Mini netliste SPICE — contrôleur drone (extrait déterministe)
* U1 : MCU STM32F411CEU6 (VDD, GND, SWDIO, SWCLK)
XU1 VDD GND SWDIO SWCLK STM32F411CEU6
* R1 : pull-up SWDIO 10k
XR1 SWDIO VDD RC0402FR-0710KL
* C1 : découplage VDD-GND 100n
XC1 VDD GND CL05B104KO5NNNC
* R2 + D1 : LED de statut (série depuis VDD)
XR2 VDD LED_A RC0603FR-07100RL
XD1 LED_A GND 150060GS75000
.END
"""

# BOM de référence : empreinte au sol par MPN (mm) — sources KiCad 8 gelées.
REF_PREFIX = {"X": "U", "R": "R", "C": "C", "D": "D"}
BOM = {
    "STM32F411CEU6": {"width_mm": 7.0, "height_mm": 7.0, "pins": 4},
    "RC0402FR-0710KL": {"width_mm": 1.0, "height_mm": 0.5, "pins": 2},
    "CL05B104KO5NNNC": {"width_mm": 1.0, "height_mm": 0.5, "pins": 2},
    "RC0603FR-07100RL": {"width_mm": 1.6, "height_mm": 0.8, "pins": 2},
    "150060GS75000": {"width_mm": 1.6, "height_mm": 0.8, "pins": 2},
}

# Placement manuel déterministe (mm) — aucune collision, carte 40 x 30.
MANUAL_PLACEMENT = {
    "U1": (10.0, 15.0),
    "R1": (20.0, 18.0),
    "C1": (20.0, 12.0),
    "R2": (20.0, 24.0),
    "D1": (30.0, 24.0),
}


def parse_mininetlist(text: str) -> tuple[dict[str, Component], dict[str, Net]]:
    """Normalise la netliste SPICE miniature en (components, nets).

    Grammaire supportée : commentaires ('*'), directives ('.END'), instances
    `<lettre><ref> <noeuds...> <MPN>` — le compteur de référence suit le
    préfixe métier (X -> U). Retourne des structures `common.design_model`.
    """
    counters: dict[str, int] = {}
    components: dict[str, Component] = {}
    nets: dict[str, Net] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("."):
            if line.upper().startswith(".END"):
                break
            continue
        tokens = line.split()
        ref = tokens[0]
        if ref and ref[0] in REF_PREFIX:
            rest = ref[1:]
            if rest and rest[0].isalpha() and rest[1:].isdigit():
                # Cas 1 : la lettre SPICE encode déjà le préfixe métier
                # (XU1 -> U1, XR1 -> R1) — référence explicite de la netliste.
                ref = rest
            elif rest.isdigit():
                # Cas 2 : référence numérique pure (X1) — compteur par préfixe.
                prefix = REF_PREFIX[ref[0]]
                counters[prefix] = counters.get(prefix, 0) + 1
                ref = f"{prefix}{counters[prefix]}"
        nodes, mpn = tokens[1:-1], tokens[-1]
        spec = BOM[mpn]
        components[ref] = Component(
            ref=ref, mpn=mpn, footprint=f"seed:{mpn}",
            pins=spec["pins"], width_mm=spec["width_mm"], height_mm=spec["height_mm"],
        )
        for index, node in enumerate(nodes, start=1):
            net = nets.setdefault(node, Net(name=node))
            net.connections.append((ref, str(index)))
    return components, nets


def build_board(text: str) -> Board:
    """Construit la Board normalisée : composants + placement manuel + nets."""
    components, nets = parse_mininetlist(text)
    board = Board(width_mm=BOARD_W_MM, height_mm=BOARD_H_MM)
    for ref, comp in components.items():
        x, y = MANUAL_PLACEMENT[ref]
        board.add_component(comp, Placement(ref=ref, x_mm=x, y_mm=y))
    board.nets = dict(nets)
    return board


def route_nets(board: Board) -> None:
    """Routage de référence déterministe — L-shape entre points de connexion.

    Règles de vias (déterministes, documentées) :
      - nets d'alimentation (VDD/GND) : 1 via d'échappement par connexion ;
      - nets de signal : 1 via par segment de plus de 10 mm.
    """
    POWER_NETS = {"VDD", "GND"}
    for net_name in sorted(board.nets):
        net = board.nets[net_name]
        points: list[tuple[float, float]] = []
        for ref, _pad in net.connections:
            comp = board.components[ref]
            place = board.placements[ref]
            points.append((place.x_mm + comp.width_mm / 4.0, place.y_mm + comp.height_mm / 4.0))
        for index, (p_from, p_to) in enumerate(zip(points, points[1:])):
            x1, y1 = p_from
            x2, y2 = p_to
            net.routed_segments.append(Segment(net=net_name, x1_mm=x1, y1_mm=y1,
                                               x2_mm=x2, y2_mm=y1, layer=0))
            net.routed_segments.append(Segment(net=net_name, x1_mm=x2, y1_mm=y1,
                                               x2_mm=x2, y2_mm=y2, layer=0))
            if abs(x2 - x1) + abs(y2 - y1) > 10.0:  # long L -> via au coude
                net.routed_segments.append(Segment(net=net_name, x1_mm=x2, y1_mm=y1,
                                                   x2_mm=x2, y2_mm=y1, layer=1, is_via=True))
        if len(points) == 1:
            # Net à connexion unique (ex. SWCLK sans debug connector) :
            # stub vers test point — considéré routé par le DRC de référence.
            x, y = points[0]
            net.routed_segments.append(Segment(net=net_name, x1_mm=x, y1_mm=y,
                                               x2_mm=x + 0.5, y2_mm=y, layer=0))
        if net_name in POWER_NETS:
            for x, y in points:  # via d'échappement vers le plan d'alim
                net.routed_segments.append(Segment(net=net_name, x1_mm=x, y1_mm=y,
                                                   x2_mm=x, y2_mm=y, layer=1, is_via=True))


def drc_check(board: Board, require_routed: bool = True) -> list[str]:
    """DRC de référence : hors-carte, chevauchements, nets non routés.

    Retourne la liste des violations (vide = pass) — le score définitif reste
    la responsabilité du drc_dfm_engine en production.
    """
    violations: list[str] = []
    refs = sorted(board.placements)
    for ref in refs:
        place = board.placements[ref]
        comp = board.components[ref]
        x1, y1, x2, y2 = comp.bounding_box(place)
        if x1 < 0.0 or y1 < 0.0 or x2 > board.width_mm or y2 > board.height_mm:
            violations.append(f"out_of_board:{ref}")
    for i, ref_a in enumerate(refs):
        for ref_b in refs[i + 1:]:
            if board.bounding_box_overlap(ref_a, ref_b):
                violations.append(f"overlap:{ref_a}/{ref_b}")
    if require_routed:
        for net_name in board.unrouted_nets():
            violations.append(f"unrouted:{net_name}")
    return violations


def export_gerber(board: Board) -> str:
    """Export Gerber minimal déterministe (couche F.Cu + flashes de vias).

    Les vias sont émis en flash `D03*` aux coordonnées entières (unité mm x
    1000) — c'est le marqueur que le test vérifie ; l'exporter de production
    ajoute les apertures complètes mais conserve ces mêmes flashes.
    """
    lines = [
        "G04 pcb_ai_designer_v2 — export deterministe (chaîne de référence)*",
        "%FSLAX46Y46*%",
        "%MOMM*%",
        "G01*",
    ]
    for net_name in sorted(board.nets):
        lines.append(f"G04 net {net_name}*")
        for seg in board.nets[net_name].routed_segments:
            x1, y1 = round(seg.x1_mm * 1000), round(seg.y1_mm * 1000)
            x2, y2 = round(seg.x2_mm * 1000), round(seg.y2_mm * 1000)
            if seg.is_via:
                lines.append(f"X{x1}Y{y1}D03*")
            else:
                lines.append(f"X{x1}Y{y1}D02*")
                lines.append(f"X{x2}Y{y2}D01*")
    lines.append("M02*")
    return "\n".join(lines) + "\n"


def run_reference_chain(text: str = MINI_NETLIST) -> tuple[Board, list[str], str]:
    """Chaîne complète : parse -> placement -> routage -> DRC -> export."""
    board = build_board(text)
    route_nets(board)
    violations = drc_check(board)
    return board, violations, export_gerber(board)


# ---------------------------------------------------------------------------
# Tests — parse
# ---------------------------------------------------------------------------

def test_parse_normalizes_components_and_nets() -> None:
    """La netliste SPICE se normalise en 5 composants / 5 nets attendus."""
    components, nets = parse_mininetlist(MINI_NETLIST)
    assert sorted(components) == ["C1", "D1", "R1", "R2", "U1"]
    assert components["U1"].mpn == "STM32F411CEU6"
    assert components["U1"].pins == 4
    # Nets nommés avec leurs connexions (ref, pad) :
    assert {ref for ref, _pad in nets["VDD"].connections} == {"U1", "R1", "C1", "R2"}
    assert {ref for ref, _pad in nets["SWDIO"].connections} == {"U1", "R1"}
    assert {ref for ref, _pad in nets["LED_A"].connections} == {"R2", "D1"}
    assert sorted(nets) == ["GND", "LED_A", "SWCLK", "SWDIO", "VDD"]


# ---------------------------------------------------------------------------
# Tests — placement + DRC
# ---------------------------------------------------------------------------

def test_placement_is_inside_board_and_collision_free() -> None:
    """Le placement manuel reste dans la carte, sans chevauchement."""
    board = build_board(MINI_NETLIST)
    assert drc_check(board, require_routed=False) == []


def test_drc_pass_after_full_routing() -> None:
    """Chaîne complète : aucun net non routé, DRC pass (pénalité vias mineure
    sur le score — les vias d'échappement VDD/GND sont volontaires)."""
    board, violations, _gerber = run_reference_chain()
    assert violations == []
    assert board.unrouted_nets() == []
    assert board.drc_score() > 99.0
    assert board.via_count() > 0  # les nets d'alim portent leurs vias


def test_drc_fail_on_forced_overlap() -> None:
    """Un composant déplacé sur l'U1 produit une violation overlap détectée."""
    board = build_board(MINI_NETLIST)
    route_nets(board)
    board.move("D1", 10.0, 15.0)  # pile sur U1
    violations = drc_check(board)
    assert "overlap:D1/U1" in violations
    assert "unrouted:VDD" not in violations  # le routage, lui, est complet


def test_drc_fail_on_unrouted_nets() -> None:
    """Sans étape de routage, le DRC échoue sur les nets vides."""
    board = build_board(MINI_NETLIST)
    violations = drc_check(board)  # require_routed=True par défaut
    assert set(violations) == {f"unrouted:{name}" for name in board.nets}
    assert len(violations) == len(board.nets)


# ---------------------------------------------------------------------------
# Tests — export Gerber
# ---------------------------------------------------------------------------

def test_gerber_export_contains_via_flashes() -> None:
    """L'export contient exactement un flash D03* par via du modèle."""
    board, _violations, gerber = run_reference_chain()
    expected_vias = board.via_count()
    assert expected_vias > 0  # VDD/GND imposent leurs vias d'échappement
    flashes = [line for line in gerber.splitlines() if line.endswith("D03*")]
    assert len(flashes) == expected_vias
    assert gerber.rstrip().endswith("M02*")


def test_chain_is_deterministic_across_replays() -> None:
    """Deux exécutions de bout en bout produisent le même Gerber (Circuitron)."""
    board_a, violations_a, gerber_a = run_reference_chain()
    board_b, violations_b, gerber_b = run_reference_chain()
    assert gerber_a == gerber_b
    assert violations_a == violations_b == []
    assert board_a.via_count() == board_b.via_count()
    assert board_a.routed_length_mm() == pytest.approx(board_b.routed_length_mm())


# ---------------------------------------------------------------------------
# Tests gardés — couches backend développées en parallèle
# ---------------------------------------------------------------------------

try:  # import gardé : le parser backend est fusionné — self-check d'intégrité
    from backend.services.parser import main as _parser_main
except Exception as _exc:  # noqa: BLE001 — ImportError ou dépendance manquante
    _parser_main = None
    _parser_import_error = _exc

requires_backend_parser = pytest.mark.skipif(
    _parser_main is None,
    reason=f"backend.services.parser indisponible ({getattr(globals().get('_parser_import_error'), '__class__', type('_e', (), {'__name__': 'unknown'})).__name__})")


@requires_backend_parser
def test_backend_parser_matches_reference_normalization() -> None:
    """Le parser backend normalise la même mini-netliste SPICE vers le même
    BOM et les mêmes nets que la référence locale (contrat de la chaîne
    déterministe : même entrée → même sortie)."""
    report = _parser_main.import_file(
        design_id="test-mini", filename="mini.net",
        content_bytes=MINI_NETLIST.encode("utf-8"), format_hint="spice")
    assert report.ok, f"import échoué : {report.warnings}"
    backend_components = {c.ref: c.mpn or c.value for c in report.board.components.values()}
    reference_components, reference_nets = parse_mininetlist(MINI_NETLIST)
    # mêmes composants (refs) — les mpn dépendent du catalogue : on compare le jeu
    assert set(backend_components) == set(reference_components)
    # mêmes nets : les nets SPICE (VDD, GND, SWDIO...) doivent exister côté backend
    for net_name in reference_nets:
        assert net_name in report.board.nets, f"net {net_name} manquant côté backend"
