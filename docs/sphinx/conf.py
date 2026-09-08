# Configuration Sphinx — documentation Python de pcb_ai_designer_v2
# Génération : make docs-sphinx  (ou python3 -m sphinx -b html docs/sphinx docs/sphinx/_build/html)

import sys
from pathlib import Path

# Racine du dépôt sur sys.path : autodoc importe `common.*` et `backend.*`
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Certains services importent leurs sous-paquets voisins en absolu
# (convention du dépôt : bootstrap sys.path par service) — autodoc a besoin
# des mêmes racines pour résoudre `from topological.pathfinder import …`, etc.
for _rel in ("backend/services/router", "backend/services/parser",
             "backend/services/firmware_bridge", "backend/services/pcb_plugin"):
    _service_root = REPO_ROOT / _rel
    if str(_service_root) not in sys.path:
        sys.path.insert(0, str(_service_root))

# ---- informations du projet --------------------------------------------------------
project = "pcb_ai_designer_v2"
author = "Équipe pcb_ai_designer"
copyright = "2026, Équipe pcb_ai_designer"
release = "2.0.0"
version = "2.0"
language = "fr"

# ---- extensions ---------------------------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",       # docstrings -> API reference
    "sphinx.ext.napoleon",      # docstrings Google style (convention du dépôt, en français)
    "sphinx.ext.viewcode",      # liens vers le source coloré
    "sphinx.ext.todo",          # blocs .. todo::
]

# Dépendances lourdes OPTIONNELLES simulées à l'import — les modules documentés
# les gardent de toute façon par imports conditionnels (convention du dépôt).
autodoc_mock_imports = [
    "fastapi", "uvicorn", "pydantic", "prometheus_client", "websockets",
    "graphql", "yaml", "jose", "passlib", "sklearn", "neo4j", "redis",
    "grpc", "grpc_reflection", "grpc_tools", "multipart", "cupy",
]

# ---- options autodoc -------------------------------------------------------------------
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_typehints = "signature"
autodoc_member_order = "bysource"

# ---- sortie HTML --------------------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "style_nav_header_background": "#0D1117",
    "navigation_depth": 3,
    "collapse_navigation": False,
}
html_title = f"{project} — référence d'implémentation"
html_short_title = project
html_static_path = []
html_copy_source = False          # les sources restent consultables via viewcode
todo_include_todos = True

# Les exceptions levées pendant l'import d'un module autodoc restent non fatales
# (module signalé manquant plutôt que build cassé) : le dépôt compile à 100 %,
# ce réglage ne sert qu'aux variations d'environnement.
nitpicky = False
