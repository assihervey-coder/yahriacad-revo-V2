#!/usr/bin/env bash
# =============================================================================
# deploy_pages.sh — déploie docs/sphinx/_build/html sur la branche gh-pages
# (GitHub Pages : source = branche gh-pages, chemin /, .nojekyll inclus).
#
# Usage :
#   make docs-pages                       # remote "origin"
#   PAGES_REMOTE=<url> make docs-pages    # remote explicite (PAT, fork…)
#
# La branche gh-pages est orpheline : elle ne contient QUE le site généré,
# son historique est réécrit à chaque déploiement (un commit par publication).
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE="${PAGES_REMOTE:-origin}"
BRANCH="gh-pages"
SRC="docs/sphinx/_build/html"

# 1) régénérer le site
make docs-sphinx

# 2) clone de travail + branche orpheline
rm -rf out/gh-pages
mkdir -p out
git clone -q --no-hardlinks "$(pwd)" out/gh-pages
cd out/gh-pages
git checkout -q --orphan "$BRANCH"
git rm -rq --ignore-unmatch .

# 3) copier le site + .nojekyll (désactive Jekyll côté GitHub)
cp -a "../../${SRC}"/* .
touch .nojekyll
git add -A
git commit -qm "docs: déploiement GitHub Pages ($(date -u '+%Y-%m-%dT%H:%M:%SZ'))"

# 4) publier
git push -q "$REMOTE" "$BRANCH:$BRANCH"
cd ../..
rm -rf out/gh-pages
echo "[deploy-pages] site publié sur la branche ${BRANCH} (${REMOTE})"
echo "[deploy-pages] URL : https://<owner>.github.io/<repo>/ (configurée une fois via l'API/Settings)"
