# Outillage Docker reproductible — [Circuitron]

Deux images construisent la plateforme : une **image de base** contenant la
chaîne d'outils EDA gelée (KiCad, SKiDL, FreeRouting, CUDA optionnel) et une
**image de service** générique déployée 9 fois (un module par conteneur).
Les versions exactes vivent dans [`versions.lock`](versions.lock) — source de
vérité unique.

## 1. Construire les deux images

```bash
# Image de base (chaîne EDA gelée : python 3.11.9, KiCad 8.0.5, SKiDL 1.2.1,
# FreeRouting 2.6.1, numpy 1.26.4) — construite depuis le dossier lui-même :
docker build -f .infra/docker_toolchain/Dockerfile.base \
             -t pcb-base:2.0.0 .infra/docker_toolchain/

# Image de service (s'appuie sur pcb-base, embarque common/ + proto/ + backend/) :
docker build -f .infra/docker_toolchain/Dockerfile.service \
             --build-arg BASE_IMAGE=pcb-base:2.0.0 \
             -t pcb-service:2.0.0 .

# Pile complète de dev (construit les services référencés par docker-compose.yml) :
docker compose build
```

L'image de service est **générique** : le conteneur à lancer est choisi par
`SERVICE_MODULE` (ex. `backend.services.parser.main`), exactement comme le fait
`docker-compose.yml`.

## 2. Reproductibilité par digests

Une image taguée (`pcb-base:2.0.0`) est mutable : pour garantir qu'un design
certifié rejoue à l'identique 6 mois plus tard, on épingle les **digests** :

```bash
# Publier et récupérer le digest immuable :
docker push registry.example.com/pcb/pcb-base:2.0.0
docker image inspect registry.example.com/pcb/pcb-base:2.0.0 \
       --format '{{index .RepoDigests 0}}'
# -> registry.example.com/pcb/pcb-base@sha256:64caractères...

# En production (Helm, .infra/kubernetes/values.yaml) :
#   image: { registry: registry.example.com/pcb, tag: "sha256:64caractères..." }
# ou directement : --build-arg BASE_IMAGE=registry.example.com/pcb/pcb-base@sha256:...
```

Règles appliquées par les Dockerfiles :

- **aucune plage de version** (`>=` interdit) — tout est épinglé dans
  `versions.lock` ;
- FreeRouting est téléchargé depuis un **tag exact** et son SHA-256 est
  enregistré (`/opt/freerouting/SHA256SUMS`) ; le pipeline de release fournit
  le digest attendu (`FREEROUTING_SHA256`) et le build échoue sinon ;
- les dépendances pip doivent passer par **`pip install --require-hashes`**
  (fichiers générés par `pip-compile --generate-hashes`) — le bloc est prêt
  dans `Dockerfile.base` ;
- la variante GPU (CUDA 12.4) est un **second digest** (`pcb-base:2.0.0-cuda12.4`),
  jamais un remplacement silencieux.

## 3. Reproduire un design certifié

Un design est « certifié » quand sa fiche de release (data/projects) référence
les digests d'images utilisés lors de sa validation. Reproduction :

```bash
# 1. Restaurer l'environnement exact du certificat :
docker run --rm -it \
  registry.example.com/pcb/pcb-base@sha256:<digest-du-certificat> /bin/bash

# 2. Rejouer la chaîne complète hors ligne (parse -> placement -> routage ->
#    DRC -> export) sur le design archivé (data/projects/<id>/versions/<n>/) :
python -m tests.vs_quilter_benchmark.run_benchmark \
       --corpus tests/vs_quilter_benchmark/corpus \
       --report out/benchmark_replay --replays 3

# 3. Vérifier : exit code 0 + métriques identiques à la fiche de release
#    (drc_score, via_count, routed_length_mm...). Le harnais rejoue 3 fois et
#    échoue si une exécution diverge — c'est le critère Circuitron
#    "même entrée -> même sortie".
```

En cas de divergence : ne JAMAIS relancer « pour voir » — ouvrir un incident
reproductibilité, identifier la couche (digest d'image, versions.lock, données
d'entrée) et re-certifier le design.

## 4. Contenu des images

| Couche | Image | Contenu gelé |
|---|---|---|
| Base CPU | `pcb-base:2.0.0-cpu` | python 3.11.9-slim, KiCad 8.0.5 (ppa), SKiDL 1.2.1, FreeRouting 2.6.1 + JRE 17, numpy 1.26.4 |
| Base GPU | `pcb-base:2.0.0-cuda12.4` | idem + runtime CUDA 12.4.1, cupy-cuda12x (bloc commenté dans Dockerfile.base) |
| Service | `pcb-service:2.0.0` | requirements.txt + common/ + proto/ + backend/, user non-root `pcb` (10001), `PYTHONPATH=/app`, ENTRYPOINT `python -m ${SERVICE_MODULE}` |
