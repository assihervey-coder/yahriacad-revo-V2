# Documentation du dépôt pcb_ai_designer_v2

Deux chaînes de génération complémentaires, **toutes deux versionnées avec
leur sortie HTML** : consultable directement depuis GitHub, régénérable en
local à l'identique.

## Sphinx — référence API Python

Source : `docs/sphinx/` (conf.py autodoc + napoleon, docstrings Google style
en français). Couvre `common`, `ai_engine` (RAG + Ollama, RL, self_verifier,
optimiseur), `rl_agent` avec la passe d'entraînement torch, `orchestrator`
et les services gRPC.

```bash
pip install sphinx sphinx-rtd-theme      # ou : pip install .[docs]
make docs-sphinx
# → docs/sphinx/_build/html/index.html
```

Pages guide : `architecture.html`, `ollama_rag.html` (branchement du LLM
local), `rl_training.html` (passe RL pas à pas), `usage.html` (cibles make).

## Doxygen — Python + noyaux C++/CUDA

Source : `docs/doxygen/Doxyfile` (README.md promu page principale, extraction
EXTRACT_ALL des docstrings Python et des commentaires des kernels de
simulation `backend/services/simulator/kernels/` — `em_kernel.cpp`,
`thermal_kernel.cu`).

```bash
apt install doxygen          # ou un binaire statique https://doxygen.nl
make docs-doxygen            # s'exécute depuis docs/doxygen/
# → docs/doxygen/build/html/index.html
```

## Conventions

- Docstrings Google style, en français, termes techniques en anglais —
  identiques dans le code, la doc Sphinx et la doc Doxygen.
- Les sorties `_build/html/` et `build/html/` sont committées : toute
  évolution de docstring passe par une regénération (`make docs`).
- Le CI rejoue le build Sphinx à chaque push et publie l'artefact
  `sphinx-html`.
