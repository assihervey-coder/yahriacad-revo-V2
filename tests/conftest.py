"""Configuration pytest — garantit l'importabilité du dépôt racine.

Les tests importent les paquets `common`, `data` et `backend` depuis la racine
du dépôt. Selon la façon dont pytest est invoqué (depuis la racine, depuis un
IDE, ou en CI avec un répertoire de travail différent), la racine peut manquer
de sys.path : ce conftest l'ajoute de façon idempotente.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
