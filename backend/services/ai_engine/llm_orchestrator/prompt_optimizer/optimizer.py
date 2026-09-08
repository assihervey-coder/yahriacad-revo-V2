"""Optimiseur de prompts — sélection par taux d'acceptation, registry persisté.

Chaque template de prompt est versionné et porte ses statistiques d'usage
(accepté / rejeté, alimenté par le feedback des keepers et des agents). La
sélection ``best_template(task)`` privilégie le meilleur taux d'acceptation
avec un nombre minimal d'essais (anti-chance statistique). Le registry est
exportable/importable en JSON pour persister l'apprentissage entre sessions.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from common.log import get_logger

logger = get_logger("ai_engine.prompts")

_MIN_ATTEMPTS = 3          # essais minimaux avant de faire confiance à un taux
_DEFAULT_TASKS = ("placement", "routing", "bom_check")


@dataclass
class PromptStats:
    """Statistiques d'acceptation d'un template — source de vérité du choix."""

    accepted: int = 0
    rejected: int = 0

    @property
    def attempts(self) -> int:
        return self.accepted + self.rejected

    @property
    def acceptance_rate(self) -> float:
        return self.accepted / self.attempts if self.attempts else 0.0


@dataclass
class PromptTemplate:
    """Template de prompt versionné pour une tâche donnée."""

    id: str
    task: str
    text: str                       # placeholders {clé} remplis par craft_*
    version: int = 1
    stats: PromptStats = field(default_factory=PromptStats)

    def fill(self, context: Dict[str, object]) -> str:
        """Remplit les placeholders connus — les clés absentes restent lisibles."""
        out = self.text
        for key, value in context.items():
            out = out.replace("{" + key + "}", str(value))
        return out


_DEFAULTS: Dict[str, str] = {
    "placement": (
        "Tu es un agent de placement PCB. Design {width}x{height} mm.\n"
        "Composants par bloc : {blocks}\nZones keepout : {keepouts}\n"
        "Place {refs} en minimisant la longueur des nets {nets} en éloignant "
        "les composants dissipant plus de {hot_w} W des blocs analogiques."
    ),
    "routing": (
        "Tu es un agent de routage. Nets prioritaires : {nets}\n"
        "Contraintes du bus : {constraints}\nPropose un ordre de routage "
        "qui respecte les impédances cibles et minimise les vias."
    ),
    "bom_check": (
        "Vérifie ce BOM contre les datasheets : {refs}\n"
        "Signale tout composant sans stock ou hors budget {budget} USD."
    ),
}


class PromptOptimizer:
    """Registry de templates + boucle feedback → meilleure sélection."""

    def __init__(self) -> None:
        self._templates: Dict[str, PromptTemplate] = {}
        for task in _DEFAULT_TASKS:
            self.register(PromptTemplate(id=f"default_{task}", task=task,
                                         text=_DEFAULTS[task]))

    # ---- registry -----------------------------------------------------------------
    def register(self, template: PromptTemplate) -> None:
        self._templates[template.id] = template

    def update_template(self, template_id: str, new_text: str) -> bool:
        """Édite le texte d'un template — bump de version (traçabilité)."""
        tpl = self._templates.get(template_id)
        if tpl is None or new_text == tpl.text:
            return False
        tpl.text, tpl.version = new_text, tpl.version + 1
        return True

    # ---- boucle de feedback -----------------------------------------------------------
    def feedback(self, accepted: bool, template_id: str) -> None:
        """Enregistre le verdict d'un agent (keeper/corrector) sur un template."""
        tpl = self._templates.get(template_id)
        if tpl is None:
            logger.warning("feedback pour template inconnu", extra={"template_id": template_id})
            return
        if accepted:
            tpl.stats.accepted += 1
        else:
            tpl.stats.rejected += 1

    def best_template(self, task: str) -> PromptTemplate:
        """Template au meilleur taux d'acceptation (≥ _MIN_ATTEMPTS, sinon premier)."""
        candidates = [t for t in self._templates.values() if t.task == task]
        if not candidates:
            raise KeyError(f"aucun template pour la tâche : {task}")
        proven = [t for t in candidates if t.stats.attempts >= _MIN_ATTEMPTS]
        pool = proven or candidates
        return max(pool, key=lambda t: (t.stats.acceptance_rate, t.stats.accepted))

    # ---- construction de prompt ----------------------------------------------------------
    def craft_placement_prompt(self, context: Dict[str, object]) -> str:
        """Construit le prompt de placement final depuis le meilleur template."""
        template = self.best_template("placement")
        return template.fill(context)

    # ---- persistance ------------------------------------------------------------------------
    def export_registry(self) -> Dict[str, object]:
        """Sérialise le registry complet (templates + stats + versions)."""
        return {"exported_at": time.time(),
                "templates": [asdict(t) for t in self._templates.values()]}

    def import_registry(self, data: Dict[str, object]) -> int:
        """Restaure un registry ; retourne le nombre de templates chargés."""
        loaded = 0
        for raw in data.get("templates", []):
            stats = raw.get("stats", {})
            self._templates[raw["id"]] = PromptTemplate(
                id=raw["id"], task=raw.get("task", "placement"),
                text=raw.get("text", ""), version=int(raw.get("version", 1)),
                stats=PromptStats(accepted=int(stats.get("accepted", 0)),
                                  rejected=int(stats.get("rejected", 0))))
            loaded += 1
        return loaded

    def save(self, path: Path) -> bool:
        """Persiste le registry en JSON ; False si le chemin n'est pas écrivable."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.export_registry(), ensure_ascii=False, indent=1),
                            encoding="utf-8")
            return True
        except OSError:
            return False

    def summary(self) -> List[Dict[str, object]]:
        """Vue compacte pour les logs et le self-test."""
        return [{"id": t.id, "task": t.task, "version": t.version,
                 "rate": round(t.stats.acceptance_rate, 3),
                 "attempts": t.stats.attempts}
                for t in self._templates.values()]
