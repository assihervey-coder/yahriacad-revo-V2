"""Agent corrector — boucle de correction runtime (sous-paquet).

`agent.py` : CorrectorAgent — diagnose ERC → patch → re-validation jusqu'à
convergence (max 5 itérations) ou escalade super_agent.
`patcher.py` : SKiDLPatcher — patch textuel par règles (regex + insertion).
"""
