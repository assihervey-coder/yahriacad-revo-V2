# Noyaux natifs du simulator — compilation & binding Python

Ce dossier contient les noyaux haute performance du moteur physique. Ils sont
**optionnels à l'exécution** : le Python embarque toujours un fallback numpy
vectorisé et démarre même si aucun binaire n'est présent.

## Contenu

| Fichier              | Langage      | Rôle                                                        |
|----------------------|--------------|-------------------------------------------------------------|
| `thermal_kernel.cu`  | CUDA (C++17) | Diffusion thermique 2D — tuiles partagées 32×32 + halos     |
| `em_kernel.cpp`      | C++17        | Matrice de couplage capacitif quasi-statique (OpenMP)       |
| `CMakeLists.txt`     | CMake ≥ 3.18 | Build des deux libs partagées, CUDA optionnel               |

## Compilation

```bash
cd backend/services/simulator/kernels
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
# → build/libthermal_kernel.so, build/libem_kernel.so   (Linux)
# → build/Release/thermal_kernel.dll, em_kernel.dll     (Windows/MSVC)
```

Prérequis :
- CUDA Toolkit ≥ 11.0 + nvcc (compute capability **70+** requis — Volta et
  au-delà ; architectures cibles : `70;75;80;86;90`) ;
- compilateur C++17 ; OpenMP optionnel (détection `find_package(OpenMP)`).

Sans CUDA Toolkit, `cmake` saute `thermal_kernel` avec un message clair et
construit seulement `em_kernel` — c'est le comportement attendu des images de
dev. En production, `Dockerfile.base` exécute ce build dans l'image toolchain
nvcc avant l'installation du service.

## Détection par le binding Python

Le Python ne tente **aucune compilation** : au démarrage d'un solveur, il
cherche la librairie déjà compilée par ctypes, dans `kernels/build/` :

```python
# thermal_sim/diffusion.py
kernel = try_load_cuda_kernel(enable_cuda)   # None si absent/désactivé
# candidats : build/libthermal_kernel.so, .dylib, thermal_kernel.dll, Release/thermal_kernel.dll
```

Signatures C attendues (ctypes `argtypes`) :

```c
int thermal_available(void);   // 1 si GPU utilisable
int thermal_step_cuda(const float* temp, const float* c_int, const float* c_src,
                      float* out, int nx, int ny, float t_amb, float c_conv);
int thermal_run_cuda(const float* temp, const float* c_int, const float* c_src,
                     float* out, int nx, int ny, float t_amb, float c_conv,
                     int n_steps);

int em_coupling_matrix(const double* segs /*6 doubles/seg : x1,y1,x2,y2,w,net_id*/,
                       int n_seg, int n_nets, double eps_r,
                       double* c_matrix_pf /* n_nets², pF, row-major */);
```

Un code de retour ≠ 0 (ou toute exception ctypes) dégrade proprement vers
numpy : `logger.warning` + bascule, jamais de crash du service.

## Politique de fallback

| Situation                          | Backend effectif                  |
|------------------------------------|-----------------------------------|
| `ENABLE_CUDA=0` (défaut)           | numpy vectorisé                   |
| `ENABLE_CUDA=1` + lib + GPU        | `thermal_step_cuda` (ctypes)      |
| `ENABLE_CUDA=1` + lib sans GPU     | warning → numpy                   |
| `ENABLE_CUDA=1` + cupy présent     | numpy (stencil) + cupy (rastérisation EM) |
| lib `em_kernel` présente           | matrice de couplage via ctypes    |
| sinon                              | boucles paires de segments numpy  |

Les deux implémentations (CUDA et numpy) partagent **les mêmes coefficients**
précalculés côté hôte : un pas GPU et un pas numpy sont bit-à-bit comparables
(float32 près pour le kernel CUDA), ce qui permet des tests de parité.
