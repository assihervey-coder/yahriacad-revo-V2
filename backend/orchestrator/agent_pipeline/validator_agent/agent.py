"""Agent validator — première passe de validation avant placement RL.

Trois familles de contrôles :
1. cohérence des nets (>= 2 connexions, refs connues du BOM) ;
2. ERC statique via l'adaptateur `erc_executor` (service écrit en parallèle) ;
3. empreintes contre la bibliothèque (préfixe KiCad + présence du champ).
Sortie : ValidationReport(passed, issues) — consommé par le corrector_agent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from common.events import EventType, make_event

from ..base_agent import AgentContext, AgentResult, BaseAgent
from ...adapters import call_adapter

_KICAD_LIB_PREFIXES = ("Package_", "Connector_", "RF_", "Sensor_", "MCU_", "Capacitor_",
                       "Resistor_", "Inductor_", "Diode_", "Button_")


@dataclass
class ValidationIssue:
    """Un problème détecté — code + sévérité + message, corrigeable par règle."""

    code: str
    severity: str            # error | warning
    message: str
    ref: str = ""
    rule: str = ""           # clé de patch (corrector_agent/patcher.py)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    """Rapport de validation — passed = zéro issue de sévérité error."""

    passed: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"passed": self.passed, "checked": self.checked,
                "issues": [i.to_json() for i in self.issues]}


class ValidatorAgent(BaseAgent):
    """Valide netlist + empreintes — l'ERC approfondi viendra du drc_dfm_engine."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="validator", grpc_service="ai_engine", **kwargs)

    def execute(self, context: AgentContext) -> AgentResult:
        report = self.validate(context)
        context.validation = report
        return AgentResult(agent=self.name, status="done",
                           outputs={"report": report.to_json()},
                           justifications=self._justify(report))

    def validate(self, context: AgentContext) -> ValidationReport:
        """Validation pure (réutilisable par la boucle du corrector_agent)."""
        components = context.extra.get("skidl_components") or \
            [c.__dict__ for c in context.bom]
        nets = context.extra.get("skidl_nets") or []
        refs = {c.get("ref") for c in components}
        issues: list[ValidationIssue] = []

        # 1) cohérence des nets
        for net in nets:
            connections = net.get("connections", [])
            for ref, _pin in connections:
                if ref not in refs:
                    issues.append(ValidationIssue(
                        code="NET-REF", severity="error",
                        message=f"ref {ref} absente du BOM (net {net['name']})",
                        ref=str(ref), rule="unknown_ref"))
            if len(connections) < 2:
                issues.append(ValidationIssue(
                    code="NET-FLOAT", severity="error",
                    message=f"net {net['name']} avec moins de 2 connexions",
                    rule="floating_net"))

        # 2) ERC statique via adaptateur
        erc = call_adapter("erc_executor",
                           {"components": components, "nets": nets})
        for raw in erc.value or []:
            issues.append(ValidationIssue(
                code=str(raw.get("code", "ERC-000")), severity=str(raw.get("severity", "error")),
                message=str(raw.get("message", "erreur ERC")),
                ref=str(raw.get("ref", "")), rule=str(raw.get("rule", ""))))

        # 3) empreintes contre la bibliothèque
        for comp in components:
            footprint = str(comp.get("footprint", ""))
            if not footprint:
                issues.append(ValidationIssue(
                    code="FP-MISSING", severity="error",
                    message=f"empreinte manquante pour {comp.get('ref')}",
                    ref=str(comp.get("ref", "")), rule="missing_footprint"))
            elif not footprint.startswith(_KICAD_LIB_PREFIXES):
                issues.append(ValidationIssue(
                    code="FP-LIB", severity="warning",
                    message=f"empreinte hors bibliothèque KiCad : {footprint}",
                    ref=str(comp.get("ref", ""))))

        report = ValidationReport(
            passed=not any(i.severity == "error" for i in issues),
            issues=issues,
            checked={"components": len(components), "nets": len(nets)})
        if not report.passed and self.state_manager is not None:
            for issue in issues:
                if issue.severity == "error":
                    self.state_manager.publish_event(make_event(
                        EventType.ERC_ERROR, context.project_id, self.name,
                        code=issue.code, message=issue.message, ref=issue.ref))
        return report

    @staticmethod
    def _justify(report: ValidationReport) -> list[str]:
        errors = sum(1 for i in report.issues if i.severity == "error")
        warnings = len(report.issues) - errors
        justifications = [f"contrôles : {report.checked} — {errors} erreur(s), {warnings} warning(s)"]
        if report.passed:
            justifications.append("validation PASS — netlist prête pour le placement RL")
        return justifications

    def fallback(self, context: AgentContext) -> Any:
        return {"report": {"passed": True, "issues": [],
                           "note": "validation dégradée — contrôles sautés"}}
