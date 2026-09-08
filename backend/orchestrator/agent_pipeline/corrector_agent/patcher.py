"""Patcher SKiDL par règles — chaque classe d'erreur ERC → patch textuel.

Stratégie : regex ciblées + insertion de composants (pull-up, capa de
découplage…). Chaque patch est idempotent : appliquer deux fois le même patch
ne duplique rien (garde sur la présence du marqueur de correction).
"""

from __future__ import annotations

import re
from typing import Any

CORRECTION_MARKER = "# [corrector] correctifs automatiques"


class SKiDLPatcher:
    """Applique des patches textuels déterministes sur un script SKiDL."""

    def __init__(self) -> None:
        self.applied: list[str] = []

    # ---- API ------------------------------------------------------------------
    def patch(self, script: str, issues: list[dict[str, Any]]) -> tuple[str, list[str]]:
        """Retourne (script_corrigé, descriptions des patches appliqués)."""
        descriptions: list[str] = []
        for issue in issues:
            rule = str(issue.get("rule") or self._classify(issue))
            handler = getattr(self, f"_patch_{rule}", None)
            if handler is None:
                continue
            new_script, description = handler(script, issue)
            if description and new_script != script:
                script = new_script
                descriptions.append(description)
            elif description:
                descriptions.append(f"{description} (idempotent — déjà présent)")
        return script, descriptions

    # ---- classification ---------------------------------------------------------
    def _classify(self, issue: dict[str, Any]) -> str:
        """Classification par règle à partir du code ERC / du message."""
        code = str(issue.get("code", "")).upper()
        message = str(issue.get("message", "")).lower()
        if "pull" in message or "i2c" in message:
            return "missing_pullup"
        if "découplage" in message or "decoupl" in message or "decoupl" in code.lower():
            return "missing_decoupling"
        if "empreinte" in message or "footprint" in message or code in ("FP-MISSING", "FP-LIB"):
            return "missing_footprint"
        if "connexions" in message or "float" in message or code == "NET-FLOAT":
            return "floating_net"
        if code == "NET-REF" or "absente" in message:
            return "unknown_ref"
        return "generic"

    # ---- patches par règle ---------------------------------------------------------
    def _patch_missing_pullup(self, script: str,
                              issue: dict[str, Any]) -> tuple[str, str]:
        """I2C sans pull-up → insertion d'une résistance 4k7 sur le bus."""
        block = [
            '# pull-up I2C 4k7 ajouté par le corrector_agent (règle missing_pullup)',
            'r_pullup_sda = Part(name="4.7k", ref_prefix="R", footprint="Resistor_SMD:R_0603_1608Metric")',
            'r_pullup_scl = Part(name="4.7k", ref_prefix="R", footprint="Resistor_SMD:R_0603_1608Metric")',
        ]
        script, changed = self._append_block(script, block)
        return script, ("pull-up 4k7 ajoutés sur I2C" if changed else "")

    def _patch_missing_decoupling(self, script: str,
                                  issue: dict[str, Any]) -> tuple[str, str]:
        """Alim sans capa de découplage → insertion 100 nF près de l'alim."""
        block = [
            '# capa de découplage 100 nF ajoutée par le corrector_agent',
            'c_decoupling = Part(name="100nF", ref_prefix="C", footprint="Capacitor_SMD:C_0603_1608Metric")',
        ]
        script, changed = self._append_block(script, block)
        return script, ("capa de découplage 100 nF ajoutée" if changed else "")

    def _patch_missing_footprint(self, script: str,
                                 issue: dict[str, Any]) -> tuple[str, str]:
        """Empreinte manquante → footprint générique 0603 documenté en revue."""
        ref = str(issue.get("ref") or "")
        if not ref:
            return script, ""
        pattern = re.compile(
            rf'({self._pyvar(ref)}\s*=\s*Part\([^)]*footprint=")("\))')
        replacement = r"\1Resistor_SMD:R_0603_1608Metric\2"
        new_script, count = pattern.subn(replacement, script, count=1)
        if count:
            return new_script, f"empreinte 0603 provisoire assignée à {ref}"
        return script, ""

    def _patch_floating_net(self, script: str,
                            issue: dict[str, Any]) -> tuple[str, str]:
        """Net flottant → commentaire de revue + connexion à GND documentée."""
        block = [
            '# net flottant signalé par ERC — revue humaine requise (règle floating_net)',
            '# option : rattacher à gnd ci-dessous une fois la topologie confirmée',
        ]
        script, changed = self._append_block(script, block)
        return script, ("commentaire de revue ajouté pour net flottant" if changed else "")

    def _patch_unknown_ref(self, script: str,
                           issue: dict[str, Any]) -> tuple[str, str]:
        """Référence inconnue → la connexion fautive est mise en commentaire."""
        ref = self._pyvar(str(issue.get("ref") or ""))
        if not ref:
            return script, ""
        pattern = re.compile(rf'^(\s*)({ref}\.pin\([^)]*\))', re.MULTILINE)
        new_script, count = pattern.subn(r'\1# \2  # ref absente du BOM', script, count=1)
        return (new_script, f"connexion à {ref} mise en commentaire" if count else "")

    def _patch_generic(self, script: str,
                       issue: dict[str, Any]) -> tuple[str, str]:
        block = [f"# [corrector] erreur {issue.get('code')} : {issue.get('message')}"]
        script, changed = self._append_block(script, block)
        return script, ("trace ERC ajoutée en commentaire" if changed else "")

    # ---- utilitaires ------------------------------------------------------------
    @staticmethod
    def _append_block(script: str, block: list[str]) -> tuple[str, bool]:
        """Append un bloc sous le marqueur — idempotent via garde textuelle."""
        first_line = block[0]
        if first_line in script:
            return script, False
        if CORRECTION_MARKER not in script:
            script += ("" if script.endswith("\n") else "\n") + \
                f"\n{CORRECTION_MARKER}\n"
        script += "\n".join(block) + "\n"
        return script, True

    @staticmethod
    def _pyvar(identifier: str) -> str:
        cleaned = "".join(c if (c.isalnum() or c == "_") else "_" for c in identifier)
        return f"n_{cleaned}" if cleaned and cleaned[0].isdigit() else (cleaned or "unnamed")
