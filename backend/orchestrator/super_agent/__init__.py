"""Sous-paquet super_agent — coordinateur du multi-agents.

Le super_agent maintient le plan d'exécution (ExecutionPlan), alloue les
ressources (endpoints gRPC + budgets de temps), arbitre les conflits de
contraintes et dialogue avec le modèle mental : chaque décision est enregistrée
avec son intention et les alternatives rejetées (audit complet).
"""
