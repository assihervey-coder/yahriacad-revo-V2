"""Intégration native EDA — KiCad live, Altium bridge, restauration de session (section 6.3, [DeepPCB]).

La façade `PcbPluginFacade` relie la plateforme aux outils EDA existants sans
export/import : routage en direct dans KiCad (kicad_live_host), synchronisation
bidirectionnelle Altium (altium_bridge) et reprise exacte après crash
(session_restorer). Zéro dépendance obligatoire hors numpy : chaque intégration
dégrade proprement quand l'outil hôte est absent.
"""
