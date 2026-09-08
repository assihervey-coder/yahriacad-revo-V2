// ============================================================================
// thermal_kernel.cu — noyau CUDA de diffusion thermique 2D (schéma explicite).
//
// Miroir exact du schéma numpy de thermal_sim/diffusion.py : les coefficients
// par cellule (conduction c_int, source c_src, convection c_border) sont
// précalculés côté hôte (numpy) et passés au kernel — le kernel n'applique
// qu'un pas FTCS sur un stencil 5 points. Le binding Python (ctypes) appelle
// `thermal_step_cuda` en boucle jusqu'à convergence.
//
// Choix d'implémentation :
//   - tuiles partagées 32×32 + halos d'une cellule (34×34 en shared memory) ;
//   - halos CLAMPÉS au bord de la carte (condition adiabatique — identique au
//     np.pad(mode="edge") du fallback numpy) ;
//   - la perte vers l'ambiant passe par c_conv (convection de Robin appliquée
//     aux deux faces de la carte, uniforme), cohérent avec la version numpy ;
//   - float32 partout : précision largement suffisante pour une carte < 200 °C.
//
// API C exportée (ctypes — voir README.md pour les signatures Python) :
//   int thermal_available(void);
//   int thermal_step_cuda(const float* temp, const float* c_int, const float* c_src,
//                         float* out, int nx, int ny, float t_amb, float c_conv);
//   int thermal_run_cuda(const float* temp, const float* c_int, const float* c_src,
//                        float* out, int nx, int ny, float t_amb, float c_conv,
//                        int n_steps);
// Codes de retour : 0 = succès, négatif = erreur CUDA (allocation/copie/lancement).
// ============================================================================

#include <cuda_runtime.h>
#include <cstdio>

#define TILE 32  // tuile de calcul — 32×32 threads, halo 1 → shared 34×34

// ---------------------------------------------------------------------------
// Kernel : un pas FTCS sur la grille (ny × nx), row-major (index = y*nx + x).
// ---------------------------------------------------------------------------
extern "C" __global__ void thermal_step_kernel(
    const float* __restrict__ temp,   // température courante (°C)
    const float* __restrict__ c_int,  // coef. de conduction par cellule (sans unité)
    const float* __restrict__ c_src,  // apport des sources par cellule (K par pas)
    float* __restrict__ out,          // température suivante (°C)
    int nx, int ny,
    float t_amb,                      // température ambiante (°C)
    float c_conv)                     // coef. de convection surfacique (sans unité)
{
    // Tuile partagée avec halos — réduit les accès globaux d'un facteur ~4.
    __shared__ float tile[TILE + 2][TILE + 2];

    const int x = blockIdx.x * TILE + threadIdx.x;
    const int y = blockIdx.y * TILE + threadIdx.y;
    const int tx = threadIdx.x + 1;
    const int ty = threadIdx.y + 1;
    const bool inside = (x < nx && y < ny);

    // Chargement du centre — coordonnées clampées aux bords (adiabatique).
    const int xc = max(0, min(nx - 1, x));
    const int yc = max(0, min(ny - 1, y));
    tile[ty][tx] = temp[yc * nx + xc];

    // Halos : chaque thread de bord charge une cellule voisine (clampée).
    if (threadIdx.x == 0)
        tile[ty][tx - 1] = temp[yc * nx + max(0, xc - 1)];
    if (threadIdx.x == TILE - 1)
        tile[ty][tx + 1] = temp[yc * nx + min(nx - 1, xc + 1)];
    if (threadIdx.y == 0)
        tile[ty - 1][tx] = temp[max(0, yc - 1) * nx + xc];
    if (threadIdx.y == TILE - 1)
        tile[ty + 1][tx] = temp[min(ny - 1, yc + 1) * nx + xc];

    __syncthreads();

    if (!inside)
        return;

    const float t = tile[ty][tx];
    const float lap = tile[ty][tx - 1] + tile[ty][tx + 1]
                    + tile[ty - 1][tx] + tile[ty + 1][tx] - 4.0f * t;

    float next = t + c_int[y * nx + x] * lap + c_src[y * nx + x];
    // convection de Robin : refroidissement des deux faces vers l'ambiant
    if (c_conv > 0.0f)
        next += c_conv * (t_amb - t);

    out[y * nx + x] = next;
}

// ---------------------------------------------------------------------------
// Helpers internes — vérification d'erreur CUDA.
// ---------------------------------------------------------------------------
static inline int cuda_check(cudaError_t err, const char* what)
{
    if (err != cudaSuccess) {
        std::fprintf(stderr, "[thermal_kernel] %s : %s\n", what, cudaGetErrorString(err));
        return -1;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Wrapper hôte : un pas (les tableaux hôte sont copiés aller-retour).
// ---------------------------------------------------------------------------
extern "C" int thermal_step_cuda(
    const float* h_temp, const float* h_c_int, const float* h_c_src,
    float* h_out, int nx, int ny, float t_amb, float c_conv)
{
    const size_t bytes = (size_t)nx * (size_t)ny * sizeof(float);
    float *d_temp = nullptr, *d_c_int = nullptr, *d_c_src = nullptr, *d_out = nullptr;

    if (cuda_check(cudaMalloc(&d_temp, bytes), "cudaMalloc d_temp")) return -1;
    if (cuda_check(cudaMalloc(&d_c_int, bytes), "cudaMalloc d_c_int")) return -2;
    if (cuda_check(cudaMalloc(&d_c_src, bytes), "cudaMalloc d_c_src")) return -3;
    if (cuda_check(cudaMalloc(&d_out, bytes), "cudaMalloc d_out")) return -4;

    int rc = 0;
    if (cuda_check(cudaMemcpy(d_temp, h_temp, bytes, cudaMemcpyHostToDevice), "H2D temp")) rc = -5;
    if (!rc && cuda_check(cudaMemcpy(d_c_int, h_c_int, bytes, cudaMemcpyHostToDevice), "H2D c_int")) rc = -6;
    if (!rc && cuda_check(cudaMemcpy(d_c_src, h_c_src, bytes, cudaMemcpyHostToDevice), "H2D c_src")) rc = -7;

    if (!rc) {
        dim3 block(TILE, TILE);
        dim3 grid((nx + TILE - 1) / TILE, (ny + TILE - 1) / TILE);
        thermal_step_kernel<<<grid, block>>>(d_temp, d_c_int, d_c_src, d_out,
                                             nx, ny, t_amb, c_conv);
        if (cuda_check(cudaGetLastError(), "lancement kernel")) rc = -8;
        else if (cuda_check(cudaDeviceSynchronize(), "synchronisation")) rc = -9;
    }
    if (!rc && cuda_check(cudaMemcpy(h_out, d_out, bytes, cudaMemcpyDeviceToHost), "D2H out"))
        rc = -10;

    cudaFree(d_temp); cudaFree(d_c_int); cudaFree(d_c_src); cudaFree(d_out);
    return rc;
}

// ---------------------------------------------------------------------------
// Wrapper hôte : n pas en double-buffering GPU (pas de va-et-vient PCIe).
// ---------------------------------------------------------------------------
extern "C" int thermal_run_cuda(
    const float* h_temp, const float* h_c_int, const float* h_c_src,
    float* h_out, int nx, int ny, float t_amb, float c_conv, int n_steps)
{
    const size_t bytes = (size_t)nx * (size_t)ny * sizeof(float);
    float *d_a = nullptr, *d_b = nullptr, *d_ci = nullptr, *d_cs = nullptr;

    if (cuda_check(cudaMalloc(&d_a, bytes), "cudaMalloc d_a")) return -1;
    if (cuda_check(cudaMalloc(&d_b, bytes), "cudaMalloc d_b")) return -2;
    if (cuda_check(cudaMalloc(&d_ci, bytes), "cudaMalloc d_ci")) return -3;
    if (cuda_check(cudaMalloc(&d_cs, bytes), "cudaMalloc d_cs")) return -4;

    int rc = 0;
    if (cuda_check(cudaMemcpy(d_a, h_temp, bytes, cudaMemcpyHostToDevice), "H2D temp")) rc = -5;
    if (!rc && cuda_check(cudaMemcpy(d_ci, h_c_int, bytes, cudaMemcpyHostToDevice), "H2D c_int")) rc = -6;
    if (!rc && cuda_check(cudaMemcpy(d_cs, h_c_src, bytes, cudaMemcpyHostToDevice), "H2D c_src")) rc = -7;

    if (!rc) {
        dim3 block(TILE, TILE);
        dim3 grid((nx + TILE - 1) / TILE, (ny + TILE - 1) / TILE);
        for (int step = 0; step < n_steps && !rc; ++step) {
            thermal_step_kernel<<<grid, block>>>(d_a, d_ci, d_cs, d_b,
                                                 nx, ny, t_amb, c_conv);
            if (cuda_check(cudaGetLastError(), "lancement kernel")) { rc = -8; break; }
            // double buffer : a ← b (échange de pointeurs, zéro copie)
            float* tmp = d_a; d_a = d_b; d_b = tmp;
        }
        if (!rc && cuda_check(cudaDeviceSynchronize(), "synchronisation")) rc = -9;
        if (!rc && cuda_check(cudaMemcpy(h_out, d_a, bytes, cudaMemcpyDeviceToHost), "D2H out"))
            rc = -10;
    }

    cudaFree(d_a); cudaFree(d_b); cudaFree(d_ci); cudaFree(d_cs);
    return rc;
}

// ---------------------------------------------------------------------------
// Disponibilité : appelé au chargement du binding pour valider le contexte GPU.
// ---------------------------------------------------------------------------
extern "C" int thermal_available(void)
{
    int device_count = 0;
    if (cudaGetDeviceCount(&device_count) != cudaSuccess || device_count < 1)
        return 0;
    return 1;
}
