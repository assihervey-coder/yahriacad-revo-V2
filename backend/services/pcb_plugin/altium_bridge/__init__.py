"""Pont Altium Designer — synchronisation bidirectionnelle placements/routage.

`bridge.AltiumBridge` import/exporte un format d'échange ASCII simplifié
(sections [PCB]/[COMPONENT]/[NET]/[TRACK]/[ZONE]) vers et depuis le modèle
interne, en journalisant les écarts de sémantique ; `mapping` traduit les
propriétés Altium (classes, règles, rooms) et arbitre les conflits.
"""
