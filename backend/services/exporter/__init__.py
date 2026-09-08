"""Service exporter — génération des fichiers finaux (section 6.6).

Gerbers RS-274X + perçage Excellon, ODB++ pour les flux avancés, BOM +
pick & place alignés sur le BOM validé par le selector_agent. L'exporteur
revérifie le profil de l'usine cible via le drc_dfm_engine, emballe le
livrable (archive + checksums) et déclenche l'événement export_ready.
"""

from .main import ExportResult, export

__all__ = ["export", "ExportResult"]
