"""Sous-système 5.2 — orchestrateur LLM [Siemens Fuse + Circuitron].

Trois organes : le moteur RAG (chunking de datasheets, index TF-IDF numpy
maison, réponses ancrées avec citations), le graphe de connaissances (client
Neo4j à miroir en mémoire + validateur de motifs SKiDL) et l'optimiseur de
prompts (templates versionnés sélectionnés par taux d'acceptation). Tous les
appels LLM sont optionnels : fallback extractif déterministe hors ligne.
"""
