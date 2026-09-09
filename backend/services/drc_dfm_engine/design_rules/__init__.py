"""Règles de design — catalogue paramétrable + vérification géométrique (section 6.4)."""

from .rules import DesignRule, default_rules
from .checker import DrcReport, Violation, check_design_rules

__all__ = ["DesignRule", "default_rules", "check_design_rules", "DrcReport", "Violation"]
