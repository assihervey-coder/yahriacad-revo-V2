# Noyaux natifs du simulator — build des bindings C (ctypes) utilisés par le Python.
#
#   cmake -B build -DCMAKE_BUILD_TYPE=Release
#   cmake --build build -j
#
# Produits :
#   - libthermal_kernel.so   (CUDA, si CUDAToolkit trouvé — compute capability 70+)
#   - libem_kernel.so        (C++17, OpenMP optionnel)
#
# En production, ce build est exécuté par Dockerfile.base (image toolchain
# nvcc). Sans CUDA, seul em_kernel est construit et le simulator utilise le
# fallback numpy vectorisé (politique détaillée dans README.md).

cmake_minimum_required(VERSION 3.18)

project(pcb_simulator_kernels
    VERSION 1.0.0
    DESCRIPTION "Noyaux thermique (CUDA) et électromagnétique (C++/OpenMP) du simulator"
    LANGUAGES CXX
)

include(GNUInstallDirs)

# ---------------------------------------------------------------------------
# em_kernel — couplage capacitif quasi-statique (C++17, OpenMP optionnel)
# ---------------------------------------------------------------------------
add_library(em_kernel SHARED em_kernel.cpp)
target_compile_features(em_kernel PRIVATE cxx_std_17)
set_target_properties(em_kernel PROPERTIES
    POSITION_INDEPENDENT_CODE ON
    OUTPUT_NAME "em_kernel"
)

find_package(OpenMP QUIET)
if(OpenMP_CXX_FOUND)
    target_link_libraries(em_kernel PRIVATE OpenMP::OpenMP_CXX)
    target_compile_definitions(em_kernel PRIVATE PCB_EM_HAVE_OPENMP=1)
    message(STATUS "em_kernel : OpenMP activé (${OpenMP_CXX_LIBRARIES})")
else()
    message(STATUS "em_kernel : OpenMP absent — exécution mono-thread (fallback OK)")
endif()

# ---------------------------------------------------------------------------
# thermal_kernel — diffusion thermique 2D (CUDA, compute capability 70+)
# ---------------------------------------------------------------------------
option(PCB_WITH_CUDA "Compiler le noyau thermique CUDA" ON)

if(PCB_WITH_CUDA)
    find_package(CUDAToolkit QUIET)
    if(CUDAToolkit_FOUND)
        enable_language(CUDA)
        add_library(thermal_kernel SHARED thermal_kernel.cu)
        target_compile_features(thermal_kernel PRIVATE cuda_std_17)
        set_target_properties(thermal_kernel PROPERTIES
            CUDA_ARCHITECTURES "70;75;80;86;90"
            POSITION_INDEPENDENT_CODE ON
            OUTPUT_NAME "thermal_kernel"
        )
        target_link_libraries(thermal_kernel PRIVATE CUDA::cudart)
        # Répertoires d'inclusion du toolkit (types utilitaires éventuels)
        target_include_directories(thermal_kernel PRIVATE ${CUDAToolkit_INCLUDE_DIRS})
        message(STATUS "thermal_kernel : CUDA ${CUDAToolkit_VERSION} (arch 70+)")
        install(TARGETS thermal_kernel
            LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
            RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
        )
    else()
        message(STATUS "CUDA Toolkit introuvable — thermal_kernel ignoré, "
                       "le simulator utilisera le fallback numpy (voir README.md)")
    endif()
else()
    message(STATUS "PCB_WITH_CUDA=OFF — thermal_kernel non construit")
endif()

install(TARGETS em_kernel
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)
