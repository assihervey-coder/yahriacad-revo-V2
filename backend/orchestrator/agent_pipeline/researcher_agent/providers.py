"""Fournisseurs de composants — abstractions DigiKey/Mouser (aucun appel réel).

TODO(REST) : implémenter les clients réels (httpx + clés d'API via env) :
    DigiKey  : GET https://api.digikey.com/products/v4/search/keyword
               headers: X-DIGIKEY-Client-Id, Authorization: Bearer <oauth2>
    Mouser   : POST https://api.mouser.com/api/v1/search/partnumber
               headers: Content-Type: application/json (?apiKey=...)
En attendant, chaque client retombe sur le catalogue local (data/component_library
ou mini-catalogue embarqué) — le pipeline reste exécutable hors ligne.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

# Mini-catalogue embarqué (~20 composants) — seed si data/component_library absent
EMBEDDED_CATALOG: list[dict[str, Any]] = [
    {"mpn": "STM32F405RGT6", "value": "STM32F405", "category": "mcu", "footprint": "LQFP-64_10x10mm", "pins": 64, "width_mm": 10.0, "height_mm": 10.0, "power_w": 0.45, "price_usd": 8.9, "stock": 4200, "voltage_v": 3.3, "current_a": 0.12},
    {"mpn": "ESP32-WROOM-32E", "value": "ESP32", "category": "mcu", "footprint": "ESP32-WROOM-32", "pins": 38, "width_mm": 18.0, "height_mm": 25.5, "power_w": 0.8, "price_usd": 3.1, "stock": 9800, "voltage_v": 3.3, "current_a": 0.24},
    {"mpn": "SX1276IMLTRT", "value": "LoRa SX1276", "category": "rf", "footprint": "QFN-28_5x5mm", "pins": 28, "width_mm": 5.0, "height_mm": 5.0, "power_w": 0.35, "price_usd": 4.7, "stock": 3100, "voltage_v": 3.3, "current_a": 0.12},
    {"mpn": "RFM95W-868S2", "value": "LoRa module", "category": "rf", "footprint": "SMD-16_SMA", "pins": 16, "width_mm": 16.0, "height_mm": 17.0, "power_w": 0.5, "price_usd": 5.9, "stock": 1500, "voltage_v": 3.3, "current_a": 0.12},
    {"mpn": "TPS63020DSJR", "value": "Buck-Boost 2A", "category": "power", "footprint": "SON-10_3x3mm", "pins": 10, "width_mm": 3.0, "height_mm": 3.0, "power_w": 1.2, "price_usd": 3.4, "stock": 6400, "voltage_v": 5.5, "current_a": 2.0},
    {"mpn": "MCP1700-3302E", "value": "LDO 3.3V", "category": "power", "footprint": "SOT-23", "pins": 3, "width_mm": 2.9, "height_mm": 1.3, "power_w": 0.3, "price_usd": 0.35, "stock": 22000, "voltage_v": 6.0, "current_a": 0.25},
    {"mpn": "MPU-6050", "value": "IMU 6 axes", "category": "sensor", "footprint": "QFN-24_4x4mm", "pins": 24, "width_mm": 4.0, "height_mm": 4.0, "power_w": 0.04, "price_usd": 2.2, "stock": 5600, "voltage_v": 3.3, "current_a": 0.004},
    {"mpn": "BMP280", "value": "Baro", "category": "sensor", "footprint": "LGA-8_2x2.5mm", "pins": 8, "width_mm": 2.0, "height_mm": 2.5, "power_w": 0.01, "price_usd": 1.1, "stock": 18000, "voltage_v": 3.3, "current_a": 0.001},
    {"mpn": "NEO-M8N-0-10", "value": "GPS", "category": "rf", "footprint": "LCC-24_18.4x18.4mm", "pins": 24, "width_mm": 18.4, "height_mm": 18.4, "power_w": 0.12, "price_usd": 15.0, "stock": 800, "voltage_v": 3.3, "current_a": 0.03},
    {"mpn": "GRM188R71H104KA93D", "value": "100nF", "category": "passive", "footprint": "C_0603_1608Metric", "pins": 2, "width_mm": 1.6, "height_mm": 0.8, "power_w": 0.0, "price_usd": 0.01, "stock": 150000, "voltage_v": 50.0, "current_a": 0.0},
    {"mpn": "RC0603FR-0710KL", "value": "10k", "category": "passive", "footprint": "R_0603_1608Metric", "pins": 2, "width_mm": 1.6, "height_mm": 0.8, "power_w": 0.1, "price_usd": 0.01, "stock": 200000, "voltage_v": 75.0, "current_a": 0.0},
    {"mpn": "SS34", "value": "Schottky 3A", "category": "power", "footprint": "SMA", "pins": 2, "width_mm": 4.3, "height_mm": 2.6, "power_w": 1.5, "price_usd": 0.12, "stock": 30000, "voltage_v": 40.0, "current_a": 3.0},
    {"mpn": "USB4085-GF-A", "value": "USB-C", "category": "connector", "footprint": "USB_C_Receptacle", "pins": 16, "width_mm": 9.0, "height_mm": 7.3, "power_w": 0.0, "price_usd": 1.4, "stock": 7600, "voltage_v": 20.0, "current_a": 5.0},
    {"mpn": "0683008041", "value": "JST-GH 8p", "category": "connector", "footprint": "JST_GH_1.25mm_8P", "pins": 8, "width_mm": 12.3, "height_mm": 3.2, "power_w": 0.0, "price_usd": 0.6, "stock": 4200, "voltage_v": 30.0, "current_a": 1.0},
]


class SupplierClient(ABC):
    """Interface d'un fournisseur — `search()` retourne des fiches catalogue."""

    name: str = "abstract"

    @abstractmethod
    def search(self, query: str, category: str = "") -> list[dict[str, Any]]:
        """Recherche par mot-clé/catégorie — TODO(REST) : appel API réel."""


class DigiKeyClient(SupplierClient):
    """Client DigiKey — TODO(REST) voir docstring de module (aucun appel réel)."""

    name = "digikey"

    def __init__(self, catalogue: list[dict[str, Any]] | None = None) -> None:
        self._catalogue = catalogue or load_catalogue()

    def search(self, query: str, category: str = "") -> list[dict[str, Any]]:
        return _filter_local(self._catalogue, query, category)


class MouserClient(SupplierClient):
    """Client Mouser — TODO(REST) voir docstring de module (aucun appel réel)."""

    name = "mouser"

    def __init__(self, catalogue: list[dict[str, Any]] | None = None) -> None:
        self._catalogue = catalogue or load_catalogue()

    def search(self, query: str, category: str = "") -> list[dict[str, Any]]:
        return _filter_local(self._catalogue, query, category)


def load_catalogue() -> list[dict[str, Any]]:
    """Charge data/component_library/seed_components.json sinon le mini-catalogue.

    Formats tolérés : liste directe de fiches, ou objet {"meta": ..., 
    "components": [...]} (format du seed versionné du dépôt).
    """
    candidate = Path("data/component_library/seed_components.json")
    try:
        if candidate.exists():
            parsed = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                parsed = parsed.get("components", [])
            if isinstance(parsed, list) and parsed:
                return [c for c in parsed if isinstance(c, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return list(EMBEDDED_CATALOG)


def _category_of(record: dict[str, Any]) -> str:
    """Catégorie tolérante : `category` (embarqué) ou `functional_block` (seed)."""
    return str(record.get("category") or record.get("functional_block") or "")


def _haystack(record: dict[str, Any]) -> str:
    """Texte de recherche : mpn + value + description + mots-clés."""
    parts = [str(record.get("mpn", "")), str(record.get("value", "")),
             str(record.get("description", ""))]
    parts.extend(str(k) for k in record.get("keywords", []) or [])
    return " ".join(parts)


def _filter_local(catalogue: list[dict[str, Any]], query: str,
                  category: str) -> list[dict[str, Any]]:
    """Filtre local : catégorie (deux nomenclatures tolérées) puis texte libre."""
    cat = (category or "").lower()
    hits = [c for c in catalogue if not cat or _category_of(c).lower() == cat]
    text = (query or "").lower()
    if text:
        keyword_hits = [c for c in hits if text in _haystack(c).lower()]
        hits = keyword_hits or hits          # repli : toute la catégorie
    return hits
