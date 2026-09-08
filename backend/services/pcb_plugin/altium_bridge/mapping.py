"""Mapping des propriétés Altium vers le modèle interne (et retour).

Traductions couvertes :
  - classes de nets Altium (Signal, Power, HighSpeed…) → net_class interne ;
  - règles de design Altium (Width, Clearance, Impedance, Differential Pair…)
    → familles de contraintes du constraint_bus (CURRENT_BUDGET, CLEARANCE,
    IMPEDANCE_TARGET…) ;
  - rooms → zones fonctionnelles.

Le dictionnaire de mapping est surchargé par `altium_mapping.yaml` si présent
(import yaml gardé) ; les arbitrages (deux règles concurrentes, valeurs
incohérentes) sont résolus « au plus strict » et JOURNALISÉS — jamais perdus
en silence (leçon de l'altium_bridge historique).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from common.log import get_logger

logger = get_logger("pcb_plugin.altium_bridge.mapping")

MAPPING_FILE = "altium_mapping.yaml"

# ---- mapping par défaut (configurable via altium_mapping.yaml) --------------
DEFAULT_MAPPING: Dict[str, Dict[str, Any]] = {
    "net_classes": {
        "Signal": "default",
        "Power": "power",
        "Ground": "ground",
        "HighSpeed": "high_speed",
        "Differential": "diff",
        "USB": "usb2",
        "USB3": "usb3",
        "DDR": "ddr4",
        "Clock": "default",
    },
    "rule_kinds": {
        "WidthRule": "CURRENT_BUDGET",
        "ClearanceRule": "CLEARANCE",
        "ImpedanceRule": "IMPEDANCE_TARGET",
        "DifferentialPairRule": "IMPEDANCE_TARGET",
        "LengthMatchRule": "LENGTH_MATCH_RULE",
        "KeepoutRule": "KEEPOUT_ZONE",
        "ThermalRule": "THERMAL_ZONE_UPDATE",
        "RoomDefinition": "functional_zone",
    },
    # valeurs par défaut quand une règle Altium ne porte pas la donnée attendue
    "defaults": {
        "impedance_target_ohm": 50.0,
        "clearance_mm": 0.2,
        "width_mm": 0.25,
    },
    "zone_kinds": {
        "RoomDefinition": "functional",
        "KeepoutRule": "keepout",
        "ThermalRule": "thermal",
    },
}


def load_mapping(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Charge altium_mapping.yaml s'il existe (import yaml gardé), sinon défauts.

    Un fichier partiellement renseigné est fusionné section par section avec
    les défauts — jamais de remplacement total silencieux.
    """
    mapping = {section: dict(values) for section, values in DEFAULT_MAPPING.items()}
    candidates = [path] if path else [
        Path.cwd() / MAPPING_FILE,
        Path(__file__).resolve().parents[1] / MAPPING_FILE,
    ]
    for candidate in candidates:
        if candidate is None or not candidate.exists():
            continue
        try:
            import yaml  # import gardé — dépendance optionnelle

            with open(candidate, "r", encoding="utf-8") as fh:
                override = yaml.safe_load(fh) or {}
        except ImportError:
            logger.warning(
                "pyyaml absent — altium_mapping.yaml ignoré, mapping par défaut utilisé",
                extra={"path": str(candidate)},
            )
            continue
        except Exception as exc:
            logger.warning("lecture altium_mapping.yaml échouée", extra={"error": str(exc)})
            continue
        for section, values in override.items():
            if isinstance(values, dict) and isinstance(mapping.get(section), dict):
                mapping[section].update(values)
        logger.info("mapping Altium chargé", extra={"path": str(candidate)})
        break
    return mapping


def altium_class_to_internal(altium_class: str, mapping: Optional[dict] = None) -> str:
    """Classe de net Altium → net_class interne (inconnue → 'default', journalisé)."""
    mapping = mapping or DEFAULT_MAPPING
    result = mapping["net_classes"].get(altium_class)
    if result is None:
        logger.info(
            "écart de sémantique : classe Altium inconnue → 'default'",
            extra={"altium_class": altium_class},
        )
        return "default"
    return result


def internal_class_to_altium(net_class: str, mapping: Optional[dict] = None) -> str:
    """Net_class interne → classe Altium la plus proche (export)."""
    mapping = mapping or DEFAULT_MAPPING
    for altium_class, internal in mapping["net_classes"].items():
        if internal == net_class:
            return altium_class
    logger.info("écart de sémantique : classe interne sans équivalent → 'Signal'",
                extra={"net_class": net_class})
    return "Signal"


def rule_to_constraint(rule: dict, mapping: Optional[dict] = None) -> Optional[dict]:
    """Règle Altium → contrainte interne {kind, key, value} — None si ignorée.

    `rule` : {"kind": "WidthRule", "scope": "+3V3", "value": 0.5, "unit": "mm"}
    Les unités non supportées sont converties quand c'est trivial (mil → mm),
    sinon journalisées et la règle est ignorée (écart de sémantique assumé).
    """
    mapping = mapping or DEFAULT_MAPPING
    kind = mapping["rule_kinds"].get(rule.get("kind", ""))
    if kind is None:
        logger.info("règle Altium sans équivalent — ignorée", extra={"rule": rule.get("kind")})
        return None
    if kind == "functional_zone":
        return {
            "kind": kind, "key": f"zone/{rule.get('scope', 'room')}",
            "value": {"zone_kind": mapping["zone_kinds"].get(rule.get("kind"), "functional"),
                      "name": rule.get("scope", "room")},
        }

    value = rule.get("value")
    if value is None:
        value = mapping["defaults"].get(_default_key_for(kind))
        if value is None:
            logger.warning("règle Altium sans valeur ni défaut — ignorée", extra={"rule": rule})
            return None
    unit = (rule.get("unit") or "mm").lower()
    if unit == "mil":
        value = float(value) * 0.0254
    elif unit not in ("mm", "ohm", ""):
        logger.warning("unité non supportée — règle ignorée",
                       extra={"unit": unit, "rule": rule.get("kind")})
        return None

    key = f"{_key_prefix(kind)}/{rule.get('scope', 'board')}"
    return {"kind": kind, "key": key, "value": {"source": "altium", "value": value}}


def arbitrate(existing: Optional[dict], incoming: Optional[dict], context: str) -> Optional[dict]:
    """Arbitrage entre deux contraintes concurrentes — « au plus strict », journalisé.

    Pour IMPEDANCE_TARGET / CLEARANCE / CURRENT_BUDGET (impédance et clearance :
    garder la contrainte la plus exigeante) ; pour les autres, la plus récente
    gagne. Retourne la contrainte retenue (peut être `incoming`).
    """
    if existing is None:
        return incoming
    if incoming is None:
        return existing
    kind = str(existing.get("kind", ""))
    strictness = {"IMPEDANCE_TARGET": "lower", "CLEARANCE": "lower", "CURRENT_BUDGET": "higher"}
    if kind in strictness:
        mode = strictness[kind]
        ev = float(existing.get("value", {}).get("value", 0.0))
        iv = float(incoming.get("value", {}).get("value", 0.0))
        winner = existing if (mode == "lower" and ev <= iv) or (mode == "higher" and ev >= iv) else incoming
        logger.info(
            "arbitrage de mapping : contrainte concurrente résolue au plus strict",
            extra={"context": context, "kind": kind,
                   "kept": winner.get("value", {}).get("value"), "mode": mode},
        )
        return winner
    logger.info("arbitrage de mapping : plus récente retenue", extra={"context": context})
    return incoming


def _key_prefix(kind: str) -> str:
    return {
        "CURRENT_BUDGET": "width",
        "CLEARANCE": "clearance",
        "IMPEDANCE_TARGET": "impedance",
        "LENGTH_MATCH_RULE": "length_match",
        "KEEPOUT_ZONE": "keepout",
        "THERMAL_ZONE_UPDATE": "thermal",
    }.get(kind, kind.lower())


def _default_key_for(kind: str) -> str:
    return {
        "IMPEDANCE_TARGET": "impedance_target_ohm",
        "CLEARANCE": "clearance_mm",
        "CURRENT_BUDGET": "width_mm",
    }.get(kind, "")
