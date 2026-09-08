"""Génération des en-têtes .h — Zephyr et Arduino (section 6.5)."""

from .generators import GeneratedHeader, generate_headers
from .zephyr import generate_zephyr_header
from .arduino import generate_arduino_header

__all__ = ["generate_headers", "generate_zephyr_header", "generate_arduino_header",
           "GeneratedHeader"]
