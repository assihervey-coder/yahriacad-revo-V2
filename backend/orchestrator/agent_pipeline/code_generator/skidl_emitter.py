"""Emitter SKiDL pur — transforme BOM + nets en script Python skidl exécutable.

Le script produit est :
- EXÉCUTABLE (`python script.py` régénère la netlist) ;
- LISIBLE EN REVUE (un bloc par composant, un bloc par net, commentaires FR) ;
- VERSIONNABLE (déterministe : même entrée → même sortie, pas d'horodatage).
"""

from __future__ import annotations

from typing import Any


def emit_skidl(components: list[dict[str, Any]], nets: list[dict[str, Any]],
               title: str = "carte PCB") -> str:
    """Génère le code SKiDL — `components`: dicts (ref, mpn, value, footprint),
    `nets`: dicts (name, connections=[(ref, pin)])."""
    lines: list[str] = [
        "# -*- coding: utf-8 -*-",
        f"# Script SKiDL généré — {title}",
        "# Projet : pcb_ai_designer_v2 · agent code_generator (brique Circuitron)",
        "# Revue humaine recommandée avant exécution : ce fichier EST la netlist.",
        "",
        "from skidl import Part, Net, generate_netlist",
        "",
        "# ---- Composants (BOM initial) ---------------------------------------------",
    ]
    for comp in components:
        name = _pyvar(comp["ref"])
        lines.append(
            f'{name} = Part(name="{comp.get("value") or comp.get("mpn", "")}", '
            f'ref_prefix="{comp["ref"][0]}", footprint="{comp.get("footprint", "")}")'
            f'  # {comp.get("mpn", "")} (ref {comp["ref"]})'
        )
    lines += ["", "# ---- Nets ------------------------------------------------------------------"]
    for net in nets:
        var = _pyvar(net["name"])
        lines.append(f'{var} = Net("{net["name"]}")')
        for ref, pin in net.get("connections", []):
            lines.append(f'{var} += {_pyvar(ref)}.pin({pin})')
    lines += ["", "# Export de la netlist (netlist.xml lisible par KiCad)",
              "generate_netlist()", ""]
    return "\n".join(lines)


def _pyvar(identifier: str) -> str:
    """Identifiant Python sûr depuis une ref/net ('U1', 'GND', 'SDA/1'...)."""
    cleaned = "".join(c if (c.isalnum() or c == "_") else "_" for c in identifier)
    if cleaned and cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    return cleaned or "unnamed"
