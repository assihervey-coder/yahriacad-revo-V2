"""Règles de fabrication par usine — profils PCBWay / JLCPCB / internal (section 6.4)."""

from .profiles import FactoryProfile, available_profiles, check_factory, get_profile

__all__ = ["FactoryProfile", "get_profile", "check_factory", "available_profiles"]
