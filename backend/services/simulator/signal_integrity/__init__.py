"""Intégrité du signal — ouverture d'œil des buses rapides et chute de tension IR.

Deux analyseurs indépendants : `eye_diagram` (heuristique calibrée sur la perte
d'insertion ~0.5 dB/in/GHz + réflexions de vias) et `ir_drop` (réseau
d'alimentation résolu par analyse nodale numpy, drapeau au-delà de 3 %).
"""
