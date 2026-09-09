"""Journal d'audit append-only — un fichier JSONL par projet.

Chemin : `data/projects/{project_id}/journal.jsonl`. Chaque transition majeure
(commit, décision d'agent, rollback) y est appendée avec auteur, note, delta
résumé et métriques. `replay(from_version)` sert à la reprise de session
(session_restorer) et au rollback_manager du self_verifier.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from common.log import get_logger

logger = get_logger("state_manager.journal")


class AuditJournal:
    """Append-only JSONL — aucun fichier n'est jamais réécrit (auditabilité)."""

    def __init__(self, base_dir: Path | str = "data/projects") -> None:
        self.base_dir = Path(base_dir)

    def _path(self, project_id: str) -> Path:
        return self.base_dir / project_id / "journal.jsonl"

    def append(self, project_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        """Append une entrée horodatée — persistance best-effort (jamais bloquante)."""
        entry = dict(entry)
        entry.setdefault("ts", time.time())
        path = self._path(project_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logger.warning("écriture journal impossible", extra={"error": str(exc)})
        return entry

    def read_all(self, project_id: str) -> list[dict[str, Any]]:
        """Retourne toutes les entrées du journal (fichier absent → liste vide)."""
        path = self._path(project_id)
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("ligne de journal illisible ignorée", extra={"path": str(path)})
        return entries

    def replay(self, project_id: str, from_version: int = 1) -> list[dict[str, Any]]:
        """Rejoue les entrées à partir d'une version — reprise/rollback."""

        def _version(entry: dict[str, Any]) -> int:
            value = entry.get("version", 0)
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        return [e for e in self.read_all(project_id) if _version(e) >= from_version]

    def latest_version(self, project_id: str) -> int:
        versions = [int(e.get("version", 0) or 0) for e in self.read_all(project_id)]
        return max(versions) if versions else 0
