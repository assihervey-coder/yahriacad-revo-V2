"""Conversion langage naturel → script SKiDL (brique Circuitron, section 6.1).

Pipeline 100 % déterministe : détection de mots-clés (accents ignorés) →
sélection de blocs fonctionnels (templates.py) → assemblage d'un script Python
SKiDL commenté en français. Un LLM optionnel peut affiner le brouillon via le
hook llm_refine() — aucun appel réseau n'a lieu à l'import ni par défaut.
"""

from __future__ import annotations

import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.log import get_logger  # noqa: E402

from nl_to_skidl.templates import BlockTemplate, all_blocks, get_block  # noqa: E402

logger = get_logger("parser.nl_to_skidl")

LLMRefiner = Callable[[str], str]


@dataclass
class SkidlScript:
    """Aligne le message proto SkidlScript (python_source/functional_blocks/explanation)."""

    python_source: str
    functional_blocks: List[str] = field(default_factory=list)
    explanation: str = ""


def _deaccent(text: str) -> str:
    """Minuscules sans accents — rend la détection de mots-clés insensible à la casse."""
    return "".join(char for char in unicodedata.normalize("NFD", (text or "").lower())
                   if unicodedata.category(char) != "Mn")


def detect_blocks(text: str) -> List[BlockTemplate]:
    """Sélectionne les blocs fonctionnels évoqués par la requête (mots-clés)."""
    lowered = _deaccent(text)
    picked = [block for block in all_blocks()
              if any(_deaccent(keyword) in lowered for keyword in block.keywords)]
    if not picked:
        # repli minimal viable : carte générique alimentée + MCU
        picked = [get_block("power_regulator"), get_block("mcu_stm32")]
        logger.info("aucun mot-clé reconnu — repli carte générique")
    # le régulateur est implicite dès qu'un MCU ou un capteur est présent
    keys = {block.key for block in picked}
    if "mcu_stm32" in keys and "power_regulator" not in keys:
        picked.insert(0, get_block("power_regulator"))
    return picked


def llm_refine(text: str, draft: str, llm: LLMRefiner) -> str:
    """Hook LLM optionnel : `llm(prompt) -> str`. Repli silencieux sur le brouillon.

    Le LLM est une amélioration, jamais une dépendance : toute erreur, réponse
    vide ou réponse non-script retombe sur le brouillon déterministe.
    """
    try:
        prompt = (
            f"Affine le script SKiDL suivant pour la demande « {text} ». "
            "Garde les commentaires en français. Réponds UNIQUEMENT par le code Python.\n\n"
            f"{draft}"
        )
        refined = llm(prompt)
        if isinstance(refined, str) and "Part(" in refined and "generate_netlist" in refined:
            logger.info("brouillon SKiDL affiné par le LLM optionnel")
            return refined
        logger.warning("réponse LLM inexploitable — brouillon déterministe conservé")
    except Exception as exc:  # réseau absent, quota, timeout... jamais bloquant
        logger.warning("LLM optionnel indisponible — fallback déterministe", extra={"error": str(exc)})
    return draft


def natural_language_to_skidl(text: str, llm: Optional[LLMRefiner] = None) -> SkidlScript:
    """Convertit une requête en langage naturel en script SKiDL exécutable."""
    blocks = detect_blocks(text)
    lines: List[str] = [
        '"""Script SKiDL généré par pcb_ai_designer_v2 — parser.nl_to_skidl.',
        "",
        f"Requête : « {text.strip()} »",
        "Format : Python (skidl) — lisible, versionnable, exécutable hors ligne.",
        '"""',
        "from skidl import Part, Net, generate_netlist",
        "",
        "# Alimentations globales du design",
        "v5v = Net('5V')    # rail brut (USB / batterie)",
        "v3v3 = Net('3V3')  # rail régulé",
        "gnd = Net('GND')",
        "nreset = Net('NRESET')",
        "spi_mosi = Net('SPI_MOSI')",
        "spi_miso = Net('SPI_MISO')",
        "spi_sck = Net('SPI_SCK')",
        "spi_nss = Net('SPI_NSS')",
        "sda = Net('SDA')",
        "scl = Net('SCL')",
        "usb_dp = Net('USB_DP')",
        "usb_dm = Net('USB_DM')",
        "antenna = Net('ANTENNA')",
        "motor_in1 = Net('MOTOR_IN1')",
        "motor_in2 = Net('MOTOR_IN2')",
        "motor_out1 = Net('MOTOR_OUT1')",
        "motor_out2 = Net('MOTOR_OUT2')",
        "",
    ]
    for index, block in enumerate(blocks, start=1):
        lines.append(f"# ═══ Bloc {index}/{len(blocks)} : {block.title} " + "═" * max(0, 34 - len(block.title)))
        lines.append(block.skidl_template.format(vdd="v3v3", gnd="gnd", p=str(index)))
        lines.append("")
    lines.append("# Génération de la netlist (export .net SPICE consommé par le parser)")
    lines.append("generate_netlist()")

    components = [comp for block in blocks for comp in block.components]
    bom_usd = sum(comp.price_usd for comp in components)
    explanation = (
        "Architecture retenue : " + " → ".join(block.key for block in blocks)
        + ". " + " ".join(block.explanation for block in blocks)
        + f" BOM estimé : {len(components)} composants, {bom_usd:.2f} USD."
    )
    source = "\n".join(lines)
    if llm is not None:
        source = llm_refine(text, source, llm)
    logger.info("script SKiDL généré", extra={"blocks": [b.key for b in blocks],
                                              "components": len(components)})
    return SkidlScript(python_source=source, functional_blocks=[b.key for b in blocks],
                       explanation=explanation)
