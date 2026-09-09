"""Client Neo4j à double mode — driver réel si joignable, miroir en mémoire sinon.

Import gardé : le paquet ``neo4j`` n'est requis qu'à la connexion (jamais à
l'import du module). Si le driver est absent ou que ``settings.neo4j_uri``
n'est pas joignable sous 2 s, le client bascule en « in-memory mirror » :
dictionnaire ``pattern_id -> dict`` offrant exactement la même API. Le reste
du cerveau ne connaît donc jamais la différence — contrat [Circuitron].
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from common.config import Settings, get_settings
from common.log import get_logger

logger = get_logger("ai_engine.kg")

_PROPS_RE = re.compile(r"\{(.+?)\}\s*\)", re.DOTALL)


class Neo4jClient:
    """Stockage de motifs de conception (patterns) — Neo4j ou miroir mémoire."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings("ai_engine")
        self._driver: Any = None
        self._memory: Dict[str, Dict[str, Any]] = {}
        self.connected = False
        self._connect()

    # ---- connexion -----------------------------------------------------------------
    def _connect(self) -> None:
        """Tente le driver réel sous 2 s, sinon bascule silencieuse en miroir."""
        try:
            import neo4j   # import gardé — dépendance optionnelle (requirements.txt)
        except ImportError:
            logger.info("driver neo4j absent — miroir en mémoire utilisé")
            return
        try:
            self._driver = neo4j.GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_user, self.settings.neo4j_password),
                connection_timeout=2.0,
            )
            self._driver.verify_connectivity()
            self.connected = True
        except Exception as exc:
            self._driver = None
            self.connected = False
            logger.info("neo4j injoignable — miroir en mémoire utilisé",
                        extra={"uri": self.settings.neo4j_uri, "error": str(exc)[:120]})

    # ---- API motifs -------------------------------------------------------------------
    def store_pattern(self, pattern: Dict[str, Any]) -> str:
        """Persiste un motif (tags, parts, nets, métriques) ; retourne son id."""
        pattern_id = str(pattern.get("id") or uuid.uuid4().hex[:12])
        payload = dict(pattern, id=pattern_id)
        if self._driver is not None:
            try:
                with self._driver.session() as session:
                    session.run(
                        "MERGE (p:Pattern {id: $id}) SET p += $props",
                        id=pattern_id, props=payload,
                    )
                return pattern_id
            except Exception as exc:
                logger.warning("écriture neo4j échouée — miroir utilisé",
                               extra={"error": str(exc)[:120]})
        self._memory[pattern_id] = payload
        return pattern_id

    def find_patterns(self, tags: List[str], limit: int = 10) -> List[Dict[str, Any]]:
        """Motifs portant au moins un des tags demandés (triés par similarité)."""
        if self._driver is not None:
            try:
                with self._driver.session() as session:
                    result = session.run(
                        "MATCH (p:Pattern) WHERE ANY(t IN p.tags WHERE t IN $tags) "
                        "RETURN p LIMIT $limit",
                        tags=tags, limit=limit,
                    )
                    return [dict(record["p"]) for record in result]
            except Exception as exc:
                logger.warning("lecture neo4j échouée — miroir utilisé",
                               extra={"error": str(exc)[:120]})
        wanted = set(tags)
        hits = [p for p in self._memory.values() if wanted & set(p.get("tags", []))]
        hits.sort(key=lambda p: -len(wanted & set(p.get("tags", []))))
        return hits[:limit]

    def count(self) -> int:
        """Nombre de motifs connus (miroir ; approximation Neo4j via find all)."""
        return len(self._memory) if self._driver is None else len(self.find_patterns([], limit=10_000))

    def seed_from_cypher(self, cypher_text: str) -> int:
        """Amorce des motifs depuis un script Cypher ; retourne le nb importé.

        Sur driver réel le script est exécuté tel quel ; en miroir, les blocs
        ``CREATE (p:Pattern {..})`` sont extraits par regex et JSON-parsés
        (guillemets simples normalisés) — suffisant pour les seeds offline.
        """
        if self._driver is not None:
            try:
                with self._driver.session() as session:
                    session.run(cypher_text)
                return 1
            except Exception as exc:
                logger.warning("seed cypher échoué — parse miroir",
                               extra={"error": str(exc)[:120]})
        imported = 0
        for block in _PROPS_RE.findall(cypher_text):
            candidate = "{" + block + "}"
            try:
                props = json.loads(candidate.replace("'", '"'))
            except ValueError:
                continue
            self.store_pattern(props)
            imported += 1
        return imported

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None
            self.connected = False
