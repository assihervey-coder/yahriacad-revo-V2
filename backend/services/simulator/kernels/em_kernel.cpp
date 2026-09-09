// ============================================================================
// em_kernel.cpp — couplage capacitif quasi-statique entre nets (C++17, OpenMP).
//
// Miroir du calcul numpy de em_sim/quasi_static.py : pour chaque paire de
// segments parallèles appartenant à deux nets différents, le couplage
// capacitif vaut C ≈ ε₀·ε_r·A/d avec
//     A = longueur de recouvrement × largeur moyenne (m²),
//     d = distance perpendiculaire entre axes (m).
// La matrice de couplage (pF, symétrique, diagonale nulle) est agrégée par
// paire de nets — consommée par le calcul de diaphonie côté Python.
//
// API C exportée (ctypes — signatures dans README.md) :
//   int em_coupling_matrix(const double* segs,     // 6 doubles par segment :
//                                                   //   x1,y1,x2,y2,width,net_id
//                          int n_seg,
//                          int n_nets,
//                          double eps_r,
//                          double* c_matrix_pf);    // n_nets × n_nets (pF), row-major
//   int em_parallel_pairs(const double* segs, int n_seg,
//                         int n_nets, double eps_r, double max_dist_mm,
//                         double* pairs_out, int max_pairs);  // option diagnostic
// Codes de retour : 0 = succès, -1 = arguments invalides.
//
// Politique d'échelle : segments plats dans un seul tableau (6 doubles chacun)
// — zéro allocation côté kernel, OpenMP sur les paires de nets (parallel for).
// ============================================================================

#include <cmath>
#include <cstdlib>

#ifdef PCB_EM_HAVE_OPENMP
#include <omp.h>
#endif

// ε₀ en pF/mm : 8.8541878128e-12 F/m = 8.8541878128e-6 pF/mm... on garde SI
// puis conversion : C[F] = ε₀[SI]·A[m²]/d[m] ; ×1e12 → pF.
static const double EPS_0_SI = 8.8541878128e-12;

typedef struct {
    double x1, y1, x2, y2;  // extrémités (mm)
    double width;           // largeur de piste (mm)
    int    net;             // indice du net (0..n_nets-1)
} EmSegment;

static inline double seg_length(const EmSegment& s)
{
    const double dx = s.x2 - s.x1;
    const double dy = s.y2 - s.y1;
    return std::sqrt(dx * dx + dy * dy);
}

// Métriques d'une paire : renvoie true si quasi-parallèles ; calcule la
// distance perpendiculaire (mm) et le recouvrement axial (mm).
static inline bool parallel_metrics(const EmSegment& a, const EmSegment& b,
                                    double& dist_mm, double& overlap_mm)
{
    const double la = seg_length(a);
    const double lb = seg_length(b);
    if (la < 1e-9 || lb < 1e-9)
        return false;

    const double ux = (a.x2 - a.x1) / la;
    const double uy = (a.y2 - a.y1) / la;
    const double vx = (b.x2 - b.x1) / lb;
    const double vy = (b.y2 - b.y1) / lb;

    // quasi-parallélisme : |sin(θ)| ≤ 0.05 (~±2.9°)
    const double sin_theta = std::fabs(ux * vy - uy * vx);
    if (sin_theta > 0.05)
        return false;

    // distance perpendiculaire du milieu de b à la droite portée par a
    const double ma_x = (a.x1 + a.x2) * 0.5;
    const double ma_y = (a.y1 + a.y2) * 0.5;
    const double mb_x = (b.x1 + b.x2) * 0.5;
    const double mb_y = (b.y1 + b.y2) * 0.5;
    dist_mm = std::fabs(ux * (mb_y - ma_y) - uy * (mb_x - ma_x));

    // recouvrement des projections de b sur l'axe de a
    const double ta = (b.x1 - ma_x) * ux + (b.y1 - ma_y) * uy;
    const double tb = (b.x2 - ma_x) * ux + (b.y2 - ma_y) * uy;
    const double lo = std::max(-la * 0.5, std::min(ta, tb));
    const double hi = std::min(la * 0.5, std::max(ta, tb));
    overlap_mm = std::max(0.0, hi - lo);
    return true;
}

// ---------------------------------------------------------------------------
// Matrice de couplage capacitif (pF) agrégée par paire de nets.
// ---------------------------------------------------------------------------
extern "C" int em_coupling_matrix(const double* segs_flat, int n_seg,
                                  int n_nets, double eps_r,
                                  double* c_matrix_pf)
{
    if (segs_flat == nullptr || c_matrix_pf == nullptr || n_seg < 0 || n_nets <= 0)
        return -1;

    // désérialisation plate → structure (sans allocation GPU, cache-friendly)
    EmSegment* segs = (EmSegment*)std::malloc((size_t)n_seg * sizeof(EmSegment));
    if (segs == nullptr && n_seg > 0)
        return -2;
    for (int i = 0; i < n_seg; ++i) {
        const double* s = segs_flat + 6 * (size_t)i;
        segs[i].x1 = s[0]; segs[i].y1 = s[1];
        segs[i].x2 = s[2]; segs[i].y2 = s[3];
        segs[i].width = s[4];
        segs[i].net = (int)s[5];
    }

    for (int i = 0; i < n_nets * n_nets; ++i)
        c_matrix_pf[i] = 0.0;

#ifdef PCB_EM_HAVE_OPENMP
#pragma omp parallel for schedule(dynamic, 1)
#endif
    for (int net_a = 0; net_a < n_nets; ++net_a) {
        // accumulation locale par net_a pour éviter les atomics
        double* row = c_matrix_pf + (size_t)net_a * (size_t)n_nets;
        for (int i = 0; i < n_seg; ++i) {
            if (segs[i].net != net_a)
                continue;
            for (int j = 0; j < n_seg; ++j) {
                if (segs[j].net <= net_a)  // paire ordonnée net_a < net_b uniquement
                    continue;
                double dist, overlap;
                if (!parallel_metrics(segs[i], segs[j], dist, overlap))
                    continue;
                if (dist <= 1e-6 || overlap <= 0.5)  // collées ou recouvrement négligeable
                    continue;
                // C = ε₀·ε_r·A/d (SI) → pF ; A en m² = (overlap·w_moy)·1e-6
                const double area_m2 = (overlap * (segs[i].width + segs[j].width) * 0.5) * 1e-6;
                const double d_m = dist * 1e-3;
                const double c_pf = EPS_0_SI * eps_r * area_m2 / d_m * 1e12;
#ifdef PCB_EM_HAVE_OPENMP
#pragma omp atomic
#endif
                row[segs[j].net] += c_pf;
            }
        }
    }

    // symétrisation (la boucle ordonnée ne remplit que le triangle supérieur)
    for (int a = 0; a < n_nets; ++a) {
        for (int b = a + 1; b < n_nets; ++b) {
            const double v = c_matrix_pf[(size_t)a * n_nets + b];
            c_matrix_pf[(size_t)b * n_nets + a] = v;
        }
    }

    std::free(segs);
    return 0;
}

// ---------------------------------------------------------------------------
// Diagnostic : liste plate des paires de segments couplées (net_a, net_b, pF).
// Renvoie le nombre de paires écrites (≤ max_pairs).
// ---------------------------------------------------------------------------
extern "C" int em_parallel_pairs(const double* segs_flat, int n_seg,
                                 int n_nets, double eps_r, double max_dist_mm,
                                 double* pairs_out, int max_pairs)
{
    if (segs_flat == nullptr || pairs_out == nullptr || n_seg < 0 || n_nets <= 0)
        return -1;

    int written = 0;
    for (int i = 0; i < n_seg && written < max_pairs; ++i) {
        for (int j = i + 1; j < n_seg && written < max_pairs; ++j) {
            EmSegment a, b;
            const double* sa = segs_flat + 6 * (size_t)i;
            const double* sb = segs_flat + 6 * (size_t)j;
            a.x1 = sa[0]; a.y1 = sa[1]; a.x2 = sa[2]; a.y2 = sa[3];
            a.width = sa[4]; a.net = (int)sa[5];
            b.x1 = sb[0]; b.y1 = sb[1]; b.x2 = sb[2]; b.y2 = sb[3];
            b.width = sb[4]; b.net = (int)sb[5];
            if (a.net == b.net)
                continue;
            double dist, overlap;
            if (!parallel_metrics(a, b, dist, overlap))
                continue;
            if (dist > max_dist_mm || overlap <= 0.5)
                continue;
            const double area_m2 = (overlap * (a.width + b.width) * 0.5) * 1e-6;
            const double c_pf = EPS_0_SI * eps_r * area_m2 / (dist * 1e-3) * 1e12;
            double* row = pairs_out + 3 * (size_t)written;
            row[0] = (double)a.net;
            row[1] = (double)b.net;
            row[2] = c_pf;
            ++written;
        }
    }
    return written;
}
