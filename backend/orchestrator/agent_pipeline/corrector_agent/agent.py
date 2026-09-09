"""Agent corrector — boucle de correction runtime jusqu'à convergence.

Reçoit les issues ERC du validator, diagnostique (classification par règle via
`patcher.SKiDLPatcher`), patche le script SKiDL (pull-up, capa de découplage…),
relance la validation — JUSQU'À convergence (max 5 itérations) OU escalade au
super_agent (décision auditable retry/degrade/abort).
"""

from __future__ import annotations

from typing import Any

from ..base_agent import AgentContext, AgentResult, BaseAgent
from ..validator_agent.agent import ValidatorAgent, ValidationReport
from .patcher import SKiDLPatcher

MAX_ITERATIONS = 5


class CorrectorAgent(BaseAgent):
    """Boucle validate → patch → re-validate (convergence en max 5 itérations)."""

    def __init__(self, validator: ValidatorAgent | None = None,
                 max_iterations: int = MAX_ITERATIONS, **kwargs: Any) -> None:
        super().__init__(name="corrector", grpc_service="ai_engine", **kwargs)
        self.validator = validator
        self.patcher = SKiDLPatcher()
        self.max_iterations = max_iterations

    def execute(self, context: AgentContext) -> AgentResult:
        report = context.validation
        if report is None and self.validator is not None:
            report = self.validator.validate(context)
        if report is None:
            raise RuntimeError("aucun rapport de validation fourni au corrector")

        outcome = self.run_correction_loop(context, context.skidl_script, report)
        context.skidl_script = outcome["script"]
        context.validation = outcome["report"]
        status = "done" if outcome["converged"] else "degraded"
        return AgentResult(agent=self.name, status=status,
                           outputs=outcome,
                           justifications=outcome["justifications"])

    def run_correction_loop(self, context: AgentContext, script: str,
                            report: ValidationReport) -> dict[str, Any]:
        """La boucle est réutilisable par l'orchestrateur (étape 1) directement."""
        iterations = 0
        descriptions: list[str] = []
        justifications: list[str] = []
        errors = [i.to_json() for i in report.issues if i.severity == "error"]

        while errors and iterations < self.max_iterations:
            iterations += 1
            self.log.info("itération de correction",
                          extra={"iteration": iterations, "errors": len(errors)})
            script, patched = self.patcher.patch(script, errors)
            descriptions.extend(p for p in patched if p)
            if not patched:  # aucune règle applicable → pas de convergence possible
                break
            if self.validator is not None:
                context.extra["skidl_script"] = script
                report = self.validator.validate(context)
                errors = [i.to_json() for i in report.issues if i.severity == "error"]
            else:
                errors = []   # sans validator, on considère les patches suffisants

        converged = not errors
        if converged:
            justifications.append(
                f"convergence atteinte en {iterations} itération(s) — {len(descriptions)} patch(es)")
        else:
            justifications.append(
                f"non convergent après {iterations} itération(s) — "
                f"{len(errors)} erreur(s) ERC résiduelle(s)")
            decision = self.escalate(context, RuntimeError(
                f"ERC non convergent : {[e['code'] for e in errors][:5]}"))
            justifications.append(f"escalade super_agent : {decision.get('decision')}")
        return {"script": script, "report": report, "iterations": iterations,
                "converged": converged, "patches": descriptions,
                "justifications": justifications}

    def fallback(self, context: AgentContext) -> Any:
        return {"converged": False, "note": "corrector en mode dégradé — revue humaine requise"}
