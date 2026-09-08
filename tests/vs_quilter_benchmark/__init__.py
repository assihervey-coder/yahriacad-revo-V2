"""Harnais de benchmark vs Quilter — section 07 de la spécification.

Rejoue les designs de référence du corpus sur notre moteur interne (chaîne
déterministe disponible : parse → placement → routage → DRC → export) et sur
le moteur concurrent (adaptateur QuilterClient — stub déterministe hors ligne,
endpoint réel à brancher en production), compare les métriques, écrit le
rapport et BLOQUE la release en cas de régression vs baseline (gate CI).
"""
