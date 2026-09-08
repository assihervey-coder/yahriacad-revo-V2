"""Service firmware_bridge — pont HW/SW [Flux.ai] (section 6.5).

Le pin_exporter exporte les assignations de broches réelles du design, le
header_generator produit les en-têtes C pour Zephyr et Arduino. Toute
modification de brochage relance la régénération et émet FIRMWARE_REGENERATED :
le développeur embarqué ne peut plus écrire un firmware désynchronisé du
matériel.
"""

from .main import GeneratedHeader, PinAssignment, export_pins, generate_headers

__all__ = ["export_pins", "generate_headers", "PinAssignment", "GeneratedHeader"]
