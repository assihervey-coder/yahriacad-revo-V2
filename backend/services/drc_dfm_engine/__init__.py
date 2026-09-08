"""Service drc_dfm_engine — vérification design + manufacturing (section 6.4).

Deux niveaux de la spécification : design_rules (clearances, annular rings,
largeurs minimales) appliquées en continu, et manufacturing_rules (profils
réels PCBWay / JLCPCB). L'erc_executor [brique Circuitron] exécute les
vérifications électriques ERC déclenchées par le corrector_agent. Un export ne
sort jamais avec une règle violée pour l'usine cible.
"""

from .main import CheckReply, Violation, check
from .design_rules.rules import DesignRule, default_rules
from .design_rules.checker import check_design_rules
from .manufacturing_rules.profiles import FactoryProfile, get_profile
from .erc_executor.erc import run_erc

__all__ = [
    "check",
    "CheckReply",
    "Violation",
    "DesignRule",
    "default_rules",
    "check_design_rules",
    "FactoryProfile",
    "get_profile",
    "run_erc",
]
