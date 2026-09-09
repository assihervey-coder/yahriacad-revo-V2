"""Moteur physique C++/CUDA — EM, thermique, SI, boucle multiphysique continue (section 6.2).

Les solveurs numpy vectorisés constituent le fallback permanent ; si la librairie
native (kernels/build/libthermal_kernel.so) ou cupy est présente, elle est
utilisée automatiquement via un binding ctypes — le service démarre dans tous
les cas, sans jamais dépendre d'un binaire externe.
"""

__version__ = "0.1.0"

__all__ = ["Simulator"]


def __getattr__(name: str):  # import paresseux — évite de tirer numpy au simple `import`
    if name == "Simulator":
        from backend.services.simulator.main import Simulator

        return Simulator
    raise AttributeError(f"module {__name__!r} n'expose pas {name!r}")
