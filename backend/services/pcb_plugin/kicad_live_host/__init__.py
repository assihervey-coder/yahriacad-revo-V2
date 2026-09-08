"""Hôte du plugin KiCad « routage en direct » — section 6.3, brique DeepPCB.

`plugin.KiCadLivePlugin` pilote l'API pcbnew (import gardé) pour appliquer les
segments produits par le moteur sans export/import, avec verrous par zone
(sémantique Placement.locked) ; `ipc` fournit le transport WebSocket vers
ws://gateway/ws avec repli IPC fichiers (JSONL) en mode local mono-machine.
"""
