"""Simulation thermique — diffusion 2D explicite (CFL stable) avec noyau CUDA optionnel.

`diffusion.steady_state` retourne la carte de température et les hotspots ;
`diffusion.transient` produit des résultats partiels pour le streaming gRPC.
Le kernel `kernels/thermal_kernel.cu` est utilisé par binding ctypes s'il est
compilé, sinon le schéma numpy prend le relais (fallback garanti).
"""
