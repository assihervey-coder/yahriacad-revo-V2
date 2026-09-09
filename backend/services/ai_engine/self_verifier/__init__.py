"""Sous-système 5.4 — vérification déterministe avant engagement [Siemens Fuse].

Deux organes : le deterministic_checker (physique pure — géométrie, électrique,
routabilité, contraintes du bus) et le rollback_manager (annulation propre
journalisée, réinjection de la contrainte violée, expérience négative pour la
policy). Aucune action n'atteint le design sans passer par ici.
"""
