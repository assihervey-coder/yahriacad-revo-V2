"""Service router — routage géométrique multi-couches (section 6.4).

Graphe de connectivité → A* sur grille avec négociation de congestion →
réduction de vias en 3 passes (brique DeepPCB, cible ~44 %). Émet un
événement NET_ROUTED par net consommé par le websocket_live.
"""
