#!/usr/bin/env bash
# =============================================================================
# deploy_pages.sh — OBSOLÈTE depuis la migration de GitHub Pages sur Actions.
#
# La publication passe désormais par .github/workflows/pages.yml :
#   - déclenché à chaque push sur main (ou manuellement : workflow_dispatch) ;
#   - construit la doc Sphinx (même recette que le job docs de ci.yml) ;
#   - publie via actions/deploy-pages — SANS branche gh-pages.
#
# La branche gh-pages a été supprimée (nettoyage des branches : le dépôt ne
# vit plus que sur main). Ce script n'est conservé que pour la traçabilité
# de l'outillage ; il refuse de s'exécuter pour éviter toute confusion.
# =============================================================================
set -euo pipefail
cat >&2 <<'EOF'
[deploy-pages] Ce chemin de déploiement n'existe plus.
[deploy-pages] GitHub Pages est publié par le workflow Actions « pages »
[deploy-pages] (.github/workflows/pages.yml) à chaque push sur main.
[deploy-pages] Déploiement manuel : gh workflow run pages (ou onglet Actions).
[deploy-pages] URL du site : https://assihervey-coder.github.io/yahriacad-revo-V2/
EOF
exit 1
