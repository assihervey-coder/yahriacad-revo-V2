#!/usr/bin/env bash
# =============================================================================
# deploy_pages.sh — déploie docs/sphinx/_build/html sur la branche gh-pages
# (GitHub Pages : source = branche gh-pages, chemin /, .nojekyll inclus).
#
# Usage (depuis n'importe où) :
#   make docs-pages                       # remote "origin"
#   PAGES_REMOTE=<url> make docs-pages    # remote explicite (PAT, fork…)
#
# Fonctionne même quand le dépôt est un SOUS-ARBORESCENCE d'un dépôt hôte
# (le layout download/pcb_ai_designer_v2 d'origine) : la racine git est
# découverte via `git rev-parse --show-toplevel` et le clone porte dessus.
#
# La branche gh-pages est orpheline : elle ne contient QUE le site généré,
# son historique est réécrit à chaque déploiement (un commit par publication).
# =============================================================================
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GIT_ROOT="$(git -C "$REPO_DIR" rev-parse --show-toplevel)"

REMOTE="${PAGES_REMOTE:-origin}"
BRANCH="gh-pages"
SRC="${REPO_DIR}/docs/sphinx/_build/html"

# 1) régénérer le site
make -C "$REPO_DIR" docs-sphinx

# 2) clone de travail (de la racine git) + branche orpheline
rm -rf "$REPO_DIR/out/gh-pages"
mkdir -p "$REPO_DIR/out"
git clone -q --no-hardlinks "$GIT_ROOT" "$REPO_DIR/out/gh-pages"
cd "$REPO_DIR/out/gh-pages"
git checkout -q --orphan "$BRANCH"
# Index vide garanti (git rm partiel + || true laisserait passer les gros
# fichiers du dépôt hôte : binaire doxygen 144 Mo, tarball 50 Mo…).
git read-tree --empty
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +

# 3) copier le site + .nojekyll (désactive Jekyll côté GitHub)
cp -a "${SRC}"/. .
touch .nojekyll
git add -A
git commit -qm "docs: déploiement GitHub Pages ($(date -u '+%Y-%m-%dT%H:%M:%SZ'))"

# 4) publier (historique réécrit à chaque déploiement -> --force assumé)
git push -q --force "$REMOTE" "$BRANCH:$BRANCH"
cd /
rm -rf "$REPO_DIR/out/gh-pages"
echo "[deploy-pages] site publié sur la branche ${BRANCH} (${REMOTE})"
echo "[deploy-pages] URL : https://<owner>.github.io/<repo>/ (configurée une fois via l'API/Settings)"
