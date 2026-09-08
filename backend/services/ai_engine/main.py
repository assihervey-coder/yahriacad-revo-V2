"""Le cerveau IA — agrégat des cinq sous-systèmes et point d'entrée du service.

:class:`AiEngine` câble : shared_mental_model (graphe d'intention + bus),
llm_orchestrator (RAG, motifs, prompts), rl_agent (action space, world model,
policy), self_verifier (checker déterministe + rollback) et
autonomous_optimizer (proposer + fast evaluator + ratchet). Les trois RPC du
contrat gRPC (proto/ai_engine/v1) y sont implémentés en logique pure :
``propose_placement``, ``verify_action``, ``run_night_optimization``.
``main()`` lance le self-test complet sur une mini-carte (3 composants,
4 nets) si les stubs gRPC ne sont pas générés — le cœur algorithmique reste
testable hors ligne, sans dépendance réseau ni torch.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# Racine du dépôt sur sys.path — requis pour `common.*` et l'import du paquet
# lors d'une exécution directe (python3 backend/services/ai_engine/main.py).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:                                        # import paquet (orchestrator, gateway, tests)
    from .autonomous_optimizer.fast_evaluator.freerouting_bridge import FastEvaluator
    from .autonomous_optimizer.fast_evaluator.specctra_export import export_dsn
    from .autonomous_optimizer.keeper_logic.keeper import KeeperLogic
    from .autonomous_optimizer.proposer_llm.proposer import HeuristicProposer, Proposal, apply_proposal
    from .llm_orchestrator.knowledge_graph.neo4j_client import Neo4jClient
    from .llm_orchestrator.knowledge_graph.pattern_validator import PatternValidator
    from .llm_orchestrator.prompt_optimizer.optimizer import PromptOptimizer
    from .llm_orchestrator.rag_engine.chunker import chunk_datasheet
    from .llm_orchestrator.rag_engine.indexer import TfidfIndex
    from .llm_orchestrator.rag_engine.retriever import RagRetriever
    from .rl_agent.action_space.space import Action, ActionSpace
    from .rl_agent.policy_network.policy import EpsilonGreedyPolicy, Experience
    from .rl_agent.world_model.dreamer import DreamerWorldModel
    from .self_verifier.deterministic_checker.checker import DeterministicChecker
    from .self_verifier.rollback_manager.rollback import RollbackManager
    from .shared_mental_model.constraint_bus.service import ProjectConstraintBus
    from .shared_mental_model.intent_graph.graph import IntentGraph
except ImportError:                          # exécution directe du script main.py
    from backend.services.ai_engine.autonomous_optimizer.fast_evaluator.freerouting_bridge import FastEvaluator
    from backend.services.ai_engine.autonomous_optimizer.fast_evaluator.specctra_export import export_dsn
    from backend.services.ai_engine.autonomous_optimizer.keeper_logic.keeper import KeeperLogic
    from backend.services.ai_engine.autonomous_optimizer.proposer_llm.proposer import (
        HeuristicProposer, Proposal, apply_proposal)
    from backend.services.ai_engine.llm_orchestrator.knowledge_graph.neo4j_client import Neo4jClient
    from backend.services.ai_engine.llm_orchestrator.knowledge_graph.pattern_validator import PatternValidator
    from backend.services.ai_engine.llm_orchestrator.prompt_optimizer.optimizer import PromptOptimizer
    from backend.services.ai_engine.llm_orchestrator.rag_engine.chunker import chunk_datasheet
    from backend.services.ai_engine.llm_orchestrator.rag_engine.indexer import TfidfIndex
    from backend.services.ai_engine.llm_orchestrator.rag_engine.retriever import RagRetriever
    from backend.services.ai_engine.rl_agent.action_space.space import Action, ActionSpace
    from backend.services.ai_engine.rl_agent.policy_network.policy import EpsilonGreedyPolicy, Experience
    from backend.services.ai_engine.rl_agent.world_model.dreamer import DreamerWorldModel
    from backend.services.ai_engine.self_verifier.deterministic_checker.checker import DeterministicChecker
    from backend.services.ai_engine.self_verifier.rollback_manager.rollback import RollbackManager
    from backend.services.ai_engine.shared_mental_model.constraint_bus.service import ProjectConstraintBus
    from backend.services.ai_engine.shared_mental_model.intent_graph.graph import IntentGraph

from common.config import get_settings
from common.design_model import Board, Component, Net, Placement, Zone
from common.events import Event, EventType, make_event
from common.grpc_helpers import proto_available
from common.log import get_logger

logger = get_logger("ai_engine.main")


class AiEngine:
    """Le cerveau IA complet — cinq sous-systèmes, un constraint_bus, un design."""

    def __init__(self, project_id: str = "proj-ai-engine", settings: Any = None) -> None:
        self.settings = settings or get_settings("ai_engine")
        self.project_id = project_id
        # --- 5.1 shared_mental_model ----------------------------------------
        self.bus = ProjectConstraintBus(project_id, self.settings.bus_target_latency_ms)
        self.intent_graph = IntentGraph(project_id)
        # --- 5.2 llm_orchestrator ---------------------------------------------
        self.rag_index = TfidfIndex()
        self.rag = RagRetriever(self.rag_index, self.settings)
        self.kg = Neo4jClient(self.settings)
        self.pattern_validator = PatternValidator(self.kg)
        self.prompts = PromptOptimizer()
        # --- 5.3 rl_agent ---------------------------------------------------------
        self.action_space = ActionSpace()
        self.world_model = DreamerWorldModel()
        self.policy = EpsilonGreedyPolicy(self.world_model)
        # --- 5.4 self_verifier -----------------------------------------------------
        self.checker = DeterministicChecker(self.bus)
        self.rollback = RollbackManager(project_id, self.bus)
        # --- 5.5 autonomous_optimizer ----------------------------------------------
        self.proposer = HeuristicProposer()
        self.evaluator = FastEvaluator(self.settings.fast_eval_budget_s, self.settings)
        self.keeper = KeeperLogic(project_id, max_vias=200)
        # --- état et câblage du bus ----------------------------------------------------
        self.board: Optional[Board] = None
        self.last_night_summary: Dict[str, Any] = {}   # bilan de la dernière boucle nocturne
        self.events: List[Event] = []
        self._bus_messages_seen = 0
        self.bus.subscribe_for(None, self._on_constraint)

    # ---- hook constraint_bus ------------------------------------------------------------
    def _on_constraint(self, message: Any) -> None:
        """Toute contrainte publiée est vue par le cerveau (compteur + log debug)."""
        self._bus_messages_seen += 1

    def _emit(self, event_type: EventType, **payload: Any) -> Event:
        event = make_event(event_type, self.project_id, emitter="ai_engine", **payload)
        self.events.append(event)
        return event

    # ---- chargement d'un design --------------------------------------------------------------
    def load_board(self, board: Board) -> Dict[str, Any]:
        """Déclare le design courant : graphe d'intention peuplé + contraintes diffusées."""
        self.board = board
        mutations = self.intent_graph.update_from_board(board)
        constraints = self.bus.broadcast_board_constraints(board)
        self._emit(EventType.PLAN_UPDATED, note="design chargé",
                   graph_version=self.intent_graph.version)
        return {"graph_mutations": mutations, "constraints_broadcast": constraints,
                "graph_stats": self.intent_graph.stats}

    # ---- RPC 1 : rl_agent ---------------------------------------------------------------------
    def propose_placement(self, component_ref: str, candidates: int = 8) -> List[Action]:
        """Actions candidates pour un composant, triées par récompense prédite."""
        if self.board is None:
            raise RuntimeError("aucun design chargé — appeler load_board() d'abord")
        board = self.board
        anchor = self._net_anchor(board, component_ref)
        pool = self.action_space.anchored_candidates(board, component_ref,
                                                     max(candidates * 3, 24), anchor)
        if not pool:                                # carte saturée : échantillon brut
            pool = self.action_space.sample((board.width_mm, board.height_mm),
                                            candidates * 4, component_ref, anchor=None)
        for action in pool:
            self.policy.remember_features(board, action)
        return self.policy.rank_candidates(board, pool)[:max(0, candidates)]

    def _net_anchor(self, board: Board, ref: str) -> tuple:
        """Centroïde des partenaires de nets — l'ancre sémantique du sampling."""
        points: List[tuple] = []
        for net in board.nets.values():
            refs = {r for r, _p in net.connections}
            if ref in refs:
                points += [(board.placements[r].x_mm, board.placements[r].y_mm)
                           for r in refs - {ref} if r in board.placements]
        if not points:
            return (board.width_mm / 2.0, board.height_mm / 2.0)
        return (sum(p[0] for p in points) / len(points),
                sum(p[1] for p in points) / len(points))

    # ---- RPC 2 : self_verifier -----------------------------------------------------------------
    def verify_action(self, action: Action) -> Dict[str, Any]:
        """Vérification SANS engagement (équivalent VerifyActionReply du proto)."""
        if self.board is None:
            raise RuntimeError("aucun design chargé — appeler load_board() d'abord")
        verdict = self.checker.check(self.board, action)
        return {"valid": verdict.valid, "violated_constraints": verdict.violated_constraints,
                "reason": verdict.reason, "rolled_back": False}

    def try_action(self, action: Action) -> Dict[str, Any]:
        """Applique puis vérifie ; rollback propre + expérience négative si invalide."""
        board = self.board
        assert board is not None
        self.rollback.push_state(board, {"note": f"avant action sur {action.ref}"})
        try:
            board.move(action.ref, action.x_mm, action.y_mm,
                       rotation_deg=getattr(action, "rotation_deg", None))
        except PermissionError as exc:                       # verrou surgical_edit humain
            self.rollback.rollback_last(board, reason=str(exc), violated=["locked"],
                                        action=action.to_json())
            return {"valid": False, "violated_constraints": ["locked"],
                    "reason": f"composant verrouillé : {exc}", "rolled_back": True}
        verdict = self.checker.check(board, action)
        if verdict.valid:
            self._emit(EventType.COMPONENT_MOVED, ref=action.ref,
                       x_mm=action.x_mm, y_mm=action.y_mm,
                       rotation_deg=getattr(action, "rotation_deg", 0.0))
            return {"valid": True, "violated_constraints": [], "reason": verdict.reason,
                    "rolled_back": False}
        # ---- verdict invalide : CONSTRAINT_VIOLATED + rollback + apprentissage négatif ----
        self._emit(EventType.CONSTRAINT_VIOLATED, ref=action.ref,
                   violated=verdict.violated_constraints, reason=verdict.reason)
        constraint = self.bus.latest().get(
            verdict.violated_constraints[0]) if verdict.violated_constraints else None
        self.rollback.rollback_last(board, reason=verdict.reason,
                                    violated=verdict.violated_constraints,
                                    constraint=constraint, action=action.to_json())
        self.world_model.observe(board, action, {"congestion_score": 1.0,
                                                 "thermal_rise_estimate": 1.0,
                                                 "si_risk": 1.0})
        self.policy.learn_from([Experience(ref=action.ref, reward=0.0, kept=False)])
        return {"valid": False, "violated_constraints": verdict.violated_constraints,
                "reason": verdict.reason, "rolled_back": True}

    # ---- RPC 3 : autonomous_optimizer ------------------------------------------------------------
    def run_night_optimization(self, max_iter: int = 300,
                               warm_start_path: Optional[Path] = None,
                               save_path: Optional[Path] = None) -> Iterator[Dict[str, Any]]:
        """Générateur d'IterationReport — boucle proposer → vérifier → évaluer → ratchet.

        Branchement ``world_model_torch.npz`` : à l'ouverture de la nuit, les
        poids exportés par la passe RL torch (format miroir du TinyNet numpy)
        sont chargés en warm start depuis ``warm_start_path`` — défaut :
        ``settings.world_model_npz`` (``PCB_WORLD_MODEL_NPZ``) — si le fichier
        existe. En fin de nuit, les poids mis à jour par les
        ``observe()``/``train()`` des itérations gardées sont réécrits au même
        slot : chaque nuit repart du modèle de la veille. La sauvegarde passe
        dans un ``finally`` — elle s'exécute même si le consommateur referme le
        générateur avant épuisement. Bilan disponible via ``last_night_summary``.
        """
        board = self.board
        if board is None:
            raise RuntimeError("aucun design chargé — appeler load_board() d'abord")
        warm_info = self._warm_start_world_model(warm_start_path)
        try:
            detail = self.evaluator.evaluate_detailed(board)
            baseline = self._composite(detail, board)
            self.keeper.set_baseline(baseline)
            stagnant = 0                  # itérations consécutives sans gain
            for _iteration in range(max_iter):
                proposals = self.proposer.propose(board, self.intent_graph, n=3)
                if not proposals:
                    break
                progressed = False
                for proposal in proposals:
                    trial = copy.deepcopy(board)
                    if not apply_proposal(trial, proposal):
                        continue
                    # 1) self_verifier d'abord : chaque position finale issue de la
                    #    proposition (move/rotate/swap) est vérifiée sur la copie.
                    actions = self._actions_for_trial(trial, proposal)
                    if not actions or any(not self.checker.check(trial, a).valid for a in actions):
                        continue
                    # 2) fast_evaluator (< 5 s garanti) puis règle du ratchet.
                    trial_detail = self.evaluator.evaluate_detailed(trial)
                    score = self._composite(trial_detail, trial)
                    if not self.keeper.keep_if_better(proposal.to_json(), score):
                        continue                                    # ratchet : rejet
                    self.rollback.push_state(board, {"note": f"keep iter {self.keeper.iterations}"})
                    apply_proposal(board, proposal)                 # engagement réel
                    self.world_model.observe(board, actions[0], {
                        "congestion_score": trial_detail["si_risk"],
                        "thermal_rise_estimate": 1.0 - trial_detail["thermal_ok"],
                        "si_risk": trial_detail["si_risk"]})
                    self.world_model.train()
                    self._emit(EventType.PLAN_UPDATED, iteration=self.keeper.iterations,
                               best_score=self.keeper.best_score)
                    progressed = True
                    yield self.keeper.reports[-1].to_json()
                self.policy.anneal()
                stagnant = 0 if progressed else stagnant + 1
                if stagnant >= 10:
                    break             # dix passes sans aucun gain : convergé
        finally:
            self._finalize_night(warm_info, save_path)

    # ---- branchement world model (passe RL torch ↔ boucle nocturne) -----------------------
    def _warm_start_world_model(self, warm_start_path: Optional[Path] = None) -> Dict[str, Any]:
        """Charge le world model torch (world_model_torch.npz) en warm start si présent."""
        path = Path(warm_start_path) if warm_start_path else Path(self.settings.world_model_npz)
        info = {"loaded": False, "path": str(path)}
        if not path.exists():
            logger.info("world model : pas de warm start (%s absent) — poids courants", path)
            return info
        if self.world_model.load(path):
            info["loaded"] = True
            logger.info("world model : warm start OK depuis %s — boucle nocturne", path)
        else:
            logger.warning("world model : warm start impossible depuis %s — poids courants", path)
        return info

    def _finalize_night(self, warm_info: Dict[str, Any], save_path: Optional[Path] = None) -> None:
        """Réécrit le slot world_model_npz avec les poids de la nuit + bilan de session."""
        path = Path(save_path) if save_path else Path(self.settings.world_model_npz)
        saved = self.world_model.save(path)
        stats = self.keeper.stats()
        self.last_night_summary = {
            "warm_start": warm_info,
            "world_model_saved": {"saved": bool(saved), "path": str(path)},
            "iterations": stats.get("iterations", 0),
            "kept": stats.get("kept", 0),
            "rejected": stats.get("rejected", 0),
            "initial_score": stats.get("initial_score"),
            "best_score": stats.get("best_score"),
            "gain_vs_initial": stats.get("gain_vs_initial"),
        }
        logger.info("boucle nocturne : %s", json.dumps(self.last_night_summary, ensure_ascii=False))

    def _composite(self, detail: Dict[str, Any], board: Board) -> float:
        """Score composite du keeper depuis une évaluation détaillée."""
        return self.keeper.composite_score(drc=float(detail["drc_proxy"]),
                                           si=1.0 - float(detail["si_risk"]),
                                           thermal=float(detail["thermal_ok"]),
                                           vias=board.via_count())

    @staticmethod
    def _actions_for_trial(trial_board: Board, proposal: Proposal) -> List[Action]:
        """Actions à vérifier pour une proposition, lues sur la copie APRÈS
        application (positions finales) — move/rotate → 1 action, swap → 2."""
        payload = proposal.json
        kind = payload.get("action")
        refs = []
        if kind in ("move", "rotate"):
            refs = [payload.get("ref")]
        elif kind == "swap":
            refs = [payload.get("ref_a"), payload.get("ref_b")]
        actions: List[Action] = []
        for ref in refs:
            placement = trial_board.placements.get(ref) if ref else None
            if placement is None:
                return []
            actions.append(Action(ref=ref, x_mm=placement.x_mm, y_mm=placement.y_mm,
                                  rotation_deg=placement.rotation_deg, layer=placement.layer))
        return actions

    @property
    def subsystem_report(self) -> Dict[str, Any]:
        """Vue d'ensemble des cinq sous-systèmes (logs, self-test, monitoring)."""
        return {
            "shared_mental_model": {"graph": self.intent_graph.stats,
                                    "bus_published": self.bus.published_count,
                                    "bus_avg_latency_ms": round(self.bus.avg_latency_ms, 3)},
            "llm_orchestrator": {"rag_chunks": self.rag_index.size,
                                 "kg_connected": self.kg.connected,
                                 "kg_patterns": self.kg.count()},
            "rl_agent": self.policy.stats,
            "self_verifier": self.rollback.stats(),
            "autonomous_optimizer": self.keeper.stats(),
            "events_emitted": [e.type.value for e in self.events],
        }


# =========================================================================================
# Self-test — mini-carte 3 composants / 4 nets, chaque sous-système est démontré.
# =========================================================================================

_DEMO_DATASHEET = (
    "ADS1115 — 16-bit ADC\n\n"
    "3.1 Electrical Characteristics\n\n"
    "The maximum junction temperature is 150 degrees Celsius. "
    "The analog supply voltage ranges from 2.0 V to 5.5 V.\f"
    "Page 2\n\n"
    "4.2 Thermal Considerations\n\n"
    "Thermal shutdown activates at 160 degrees Celsius on the junction. "
    "Decoupling capacitors of 100 nF must be placed within 2 mm of the supply pins."
)


def build_demo_board() -> Board:
    """Mini-carte de démonstration : 3 composants, 4 nets, 1 zone keepout."""
    board = Board(width_mm=50.0, height_mm=40.0)
    board.add_component(Component(ref="U1", mpn="STM32F103C8", value="MCU",
                                  pins=48, width_mm=7.0, height_mm=7.0,
                                  power_w=0.3, functional_block="mcu"),
                        Placement(ref="U1", x_mm=8.0, y_mm=8.0))
    board.add_component(Component(ref="U2", mpn="ADS1115", value="ADC 16-bit",
                                  pins=10, width_mm=4.0, height_mm=4.0,
                                  power_w=0.05, functional_block="analog"),
                        Placement(ref="U2", x_mm=12.0, y_mm=30.0))
    board.add_component(Component(ref="U3", mpn="AP2112K-3.3", value="LDO 3.3V",
                                  pins=5, width_mm=3.0, height_mm=3.0,
                                  power_w=0.5, functional_block="power"),
                        Placement(ref="U3", x_mm=42.0, y_mm=6.0))
    board.nets["VCC"] = Net(name="VCC", connections=[("U3", "2"), ("U1", "44"), ("U2", "8")],
                            net_class="power")
    board.nets["GND"] = Net(name="GND", connections=[("U3", "1"), ("U1", "23"), ("U2", "5")],
                            net_class="power")
    board.nets["SDA"] = Net(name="SDA", connections=[("U1", "30"), ("U2", "4")], net_class="i2c")
    board.nets["SCL"] = Net(name="SCL", connections=[("U1", "29"), ("U2", "3")], net_class="i2c")
    board.zones.append(Zone(name="antenna_keepout", x_min_mm=36.0, y_min_mm=20.0,
                            x_max_mm=50.0, y_max_mm=40.0, kind="keepout"))
    return board


def _demo_skidl_valid() -> str:
    """Script SKiDL conforme (régulateur + découplage + pull-up I2C + masse)."""
    return (
        "from skidl import *\n"
        "vcc = Net('VCC'); gnd = Net('GND'); sda = Net('SDA'); scl = Net('SCL')\n"
        "u1 = Part('MCU', 'STM32F103', ref='U1')\n"
        "u3 = Part('Regulator', 'AP2112K', ref='U3')\n"
        "c1 = Part('Device', 'C', ref='C1')\n"
        "r1 = Part('Device', 'R', ref='R1')\n"
        "vcc += u1[44]; vcc += u3[2]; vcc += c1[1]\n"
        "gnd += u1[23]; gnd += u3[1]; gnd += c1[2]\n"
        "sda += u1[30]; sda += r1[1]\n"
        "scl += u1[29]; scl += r1[2]\n"
    )


def self_test() -> Dict[str, Any]:
    """Self-test complet du cerveau IA — imprime et retourne le rapport."""
    engine = AiEngine("proj-selftest")
    report: Dict[str, Any] = {}

    # ---- 0) chargement : graphe d'intention + bus de contraintes ---------------------
    report["load"] = engine.load_board(build_demo_board())
    print("[5.1] graphe d'intention + bus :", json.dumps(report["load"], ensure_ascii=False))

    # ---- 1) rl_agent : proposition de placements --------------------------------------
    proposals = engine.propose_placement("U1", candidates=5)
    report["proposals_u1"] = [a.to_json() for a in proposals]
    print("[5.3] propose_placement(U1, 5) :", json.dumps(report["proposals_u1"], ensure_ascii=False))

    # ---- 2) self_verifier : vérification des actions proposées --------------------------
    verdicts = [engine.verify_action(action) for action in proposals]
    report["verdicts_valid"] = sum(1 for v in verdicts if v["valid"])
    assert report["verdicts_valid"] == len(verdicts), "des actions légales ont été rejetées"

    # ---- 3) action invalide volontaire (keepout) → rollback propre ------------------------
    bad = Action(ref="U2", x_mm=42.0, y_mm=30.0)            # dans antenna_keepout
    reply = engine.verify_action(bad)
    assert not reply["valid"], "le keepout aurait dû être détecté"
    applied = engine.try_action(bad)
    assert applied["rolled_back"] and not applied["valid"]
    assert abs(engine.board.placements["U2"].x_mm - 12.0) < 1e-6, "rollback non restauré"
    report["invalid_action"] = {"violated": reply["violated_constraints"],
                                "rolled_back": applied["rolled_back"]}
    print("[5.4] action invalide + rollback :", json.dumps(report["invalid_action"], ensure_ascii=False))

    # ---- 4) llm_orchestrator : RAG ancré + motifs + prompts --------------------------------
    engine.rag_index.upsert(chunk_datasheet(_DEMO_DATASHEET, "ADS1115_datasheet.pdf"))
    rag_answer = engine.rag.answer("What is the maximum junction temperature?", k=2)
    report["rag"] = {"chunks": engine.rag_index.size, "citations": rag_answer["citations"],
                     "mode": rag_answer["mode"]}
    violations = engine.pattern_validator.validate(
        "u1 = Part('MCU', 'STM32', ref='U1')\n"
        "u3 = Part('Regulator', 'AP2112K', ref='U3')\n"
        "vcc = Net('VCC')\nsda = Net('SDA')\ngnd = Net('GND')\n"
        "vcc += u1[1]; sda += u1[2]; gnd += u1[3]\n")
    report["pattern_violations"] = [v.to_json() for v in violations]
    learned = engine.pattern_validator.learn_validated(_demo_skidl_valid())
    for _ in range(3):
        engine.prompts.feedback(True, "default_placement")
    engine.prompts.feedback(False, "default_placement")
    prompt = engine.prompts.craft_placement_prompt({"width": 50, "height": 40,
                                                    "refs": "U1 U2 U3", "nets": "VCC SDA SCL",
                                                    "blocks": "{mcu: U1}", "keepouts": "antenna",
                                                    "hot_w": 0.3})
    report["llm_orchestrator"] = {"violations": len(violations), "pattern_learned": learned,
                                  "prompt_chars": len(prompt)}
    print("[5.2] RAG + motifs + prompts :", json.dumps(report["llm_orchestrator"], ensure_ascii=False))

    # ---- 5) autonomous_optimizer : 20 itérations nocturnes, ratchet ------------------------
    dsn_preview = "\n".join(export_dsn(engine.board).splitlines()[:4])
    print("[5.5] export Specctra DSN (extrait) :\n" + dsn_preview)
    iterations = list(engine.run_night_optimization(20))
    stats = engine.keeper.stats()
    assert stats["best_score"] >= stats["initial_score"] - 1e-9, "RATCHET VIOLÉ"
    assert stats["kept"] >= 1, "aucune amélioration trouvée en 20 itérations"
    report["night_optimization"] = {"iterations": len(iterations), **stats}
    print("[5.5] boucle nocturne :", json.dumps(report["night_optimization"], ensure_ascii=False))

    # ---- 6) rapport consolidé des cinq sous-systèmes ---------------------------------------
    report["subsystems"] = engine.subsystem_report
    print("=== RAPPORT SELF-TEST ai_engine ===")
    print(json.dumps(report["subsystems"], ensure_ascii=False, indent=1))
    return report


# =========================================================================================
# Bootstrap — serveur gRPC si stubs générés (make proto), sinon self-test « logique seule ».
# =========================================================================================

def _grpc_bootstrap(engine: AiEngine) -> bool:
    """Démarre le service gRPC du contrat ai_engine.proto ; False si stubs absents."""
    if not proto_available():
        return False
    try:
        from proto_gen.pcb.ai.v1 import ai_engine_pb2, ai_engine_pb2_grpc   # généré par make proto
    except ImportError:
        return False

    class AiEngineServicer(ai_engine_pb2_grpc.AiEngineServicer):
        """Adapte la logique pure de AiEngine au contrat ProtoBuf pcb.ai.v1."""

        def ProposePlacement(self, request, context):
            actions = engine.propose_placement(request.component_ref, request.candidates or 8)
            return ai_engine_pb2.PlacementActions(items=[
                ai_engine_pb2.PlacementAction(
                    component_ref=a.ref, x_mm=a.x_mm, y_mm=a.y_mm,
                    rotation_deg=a.rotation_deg, layer=a.layer,
                    predicted_reward=a.predicted_reward) for a in actions])

        def VerifyAction(self, request, context):
            action = Action(ref=request.action.component_ref, x_mm=request.action.x_mm,
                            y_mm=request.action.y_mm, rotation_deg=request.action.rotation_deg,
                            layer=request.action.layer)
            reply = engine.verify_action(action)
            return ai_engine_pb2.VerifyActionReply(
                valid=reply["valid"], violated_constraints=reply["violated_constraints"],
                reason=reply["reason"], rolled_back=reply["rolled_back"])

        def RunNightOptimization(self, request, context):
            for report in engine.run_night_optimization(request.max_iterations or 300):
                yield ai_engine_pb2.OptimizerIteration(
                    iteration=report["iteration"], proposal_json=report["proposal"],
                    eval_score=report["eval_score"], kept=report["kept"],
                    best_score=report["best_score"])

    def _add_servicer(_servicer: Any, server: Any) -> None:
        ai_engine_pb2_grpc.add_AiEngineServicer_to_server(AiEngineServicer(engine), server)

    from common.grpc_helpers import serve_grpc
    serve_grpc("ai_engine", _add_servicer)
    return True


def main() -> None:
    """Point d'entrée : gRPC si disponible, sinon self-test démonstratif complet."""
    engine = AiEngine("proj-ai-engine")
    if not _grpc_bootstrap(engine):
        logger.info("stubs gRPC absents — mode logique seule (self-test)")
        self_test()


if __name__ == "__main__":
    main()
