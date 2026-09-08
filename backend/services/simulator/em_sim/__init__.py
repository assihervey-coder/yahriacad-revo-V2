"""Simulation électromagnétique quasi-statique — diaphonie, EMI, intégrité de masse.

Fournit `quasi_static.quasi_static_analysis` : estimation vectorisée numpy du
couplage capacitif entre nets parallèles (C = ε·A/d), des boucles de courant et
de l'effet des découpages du plan de masse, avec usage optionnel de cupy.
"""
