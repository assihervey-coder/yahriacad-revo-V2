"""Sous-système 5.3 — agent RL : espace d'action, world model, policy.

Espace d'action (x, y, rotation, couche) discrétisé au pas de 0.5 mm ; world
model « DreamerV3-like » en numpy pur (prédit congestion / échauffement /
risque SI sans exécuter l'action) ; policy ε-greedy qui sélectionne sur les
prédictions du world model. Tout tourne hors ligne — torch n'est qu'une
option jamais requise.
"""
