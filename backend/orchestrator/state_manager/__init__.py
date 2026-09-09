"""Sous-paquet state_manager — état global, journal d'audit, verrous, deltas.

Le state_manager versionne le design de chaque projet (DesignState), écrit le
journal append-only (JSONL), protège les zones critiques par des verrous
l'humain gagne sur l'agent RL, et diffuse les deltas aux abonnés WebSocket.
"""
