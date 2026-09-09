"""Index vectoriel TF-IDF « maison » — numpy pur, zéro dépendance externe.

Pourquoi une implémentation interne plutôt qu'un vector store lourd : le
corpus d'un projet (datasheets + notes de contraintes) reste modeste
(10^3–10^4 chunks), la recherche cosinus sur matrice dense tient en quelques
millisecondes, et le service doit démarrer hors ligne. ``persist``/``load``
sérialisent vocabulaire, IDF, matrice et documents dans un ``npz`` sous
``settings.vector_store_dir``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from common.config import get_settings

from .chunker import Document

_TOKEN_RE = re.compile(r"[a-zà-ÿ0-9]{2,}")


def _tokenize(text: str) -> List[str]:
    """Tokenisation déterministe : minuscule, alphanumérique ≥ 2 caractères."""
    return _TOKEN_RE.findall(text.lower())


class TfidfIndex:
    """Index TF-IDF + similarité cosinus — stockage en matrice dense numpy."""

    def __init__(self) -> None:
        self._docs: List[Document] = []
        self._vocab: Dict[str, int] = {}
        self._idf: Optional[np.ndarray] = None
        self._matrix: Optional[np.ndarray] = None   # (n_docs, n_terms), L2-normalisée

    # ---- propriétés -------------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self._docs)

    @property
    def vocabulary_size(self) -> int:
        return len(self._vocab)

    # ---- construction ------------------------------------------------------------
    def fit(self, docs: List[Document]) -> None:
        """(Re)construit vocabulaire, IDF et matrice depuis une liste de documents."""
        self._docs = list(docs)
        self._vocab = {}
        for doc in self._docs:
            for token in _tokenize(doc.content):
                self._vocab.setdefault(token, len(self._vocab))
        n_docs = max(1, len(self._docs))
        df = np.zeros(len(self._vocab), dtype=np.float64)
        for doc in self._docs:
            for token in set(_tokenize(doc.content)):
                df[self._vocab[token]] += 1.0
        self._idf = np.log((1.0 + n_docs) / (1.0 + df)) + 1.0   # lissage type sklearn
        self._matrix = np.zeros((len(self._docs), len(self._vocab)), dtype=np.float64)
        for i, doc in enumerate(self._docs):
            self._matrix[i] = self._vectorize(_tokenize(doc.content))

    def upsert(self, docs: List[Document]) -> None:
        """Ajoute des documents puis reconstruit l'index (idempotent et simple)."""
        self.fit(list(self._docs) + list(docs))

    # ---- vecteurs ------------------------------------------------------------------
    def _vectorize(self, tokens: List[str]) -> np.ndarray:
        """Sac de mots TF-IDF L2-normalisé pour une liste de tokens."""
        vec = np.zeros(len(self._vocab), dtype=np.float64)
        for token in tokens:
            idx = self._vocab.get(token)
            if idx is not None:
                vec[idx] += 1.0
        vec *= self._idf
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def search(self, query: str, k: int = 4) -> List[Tuple[Document, float]]:
        """Retourne les (document, score) les plus proches de la requête."""
        if self._matrix is None or not self._docs:
            return []
        q = self._vectorize(_tokenize(query))
        scores = self._matrix @ q
        order = np.argsort(-scores)[: max(1, k)]
        return [(self._docs[int(i)], float(scores[int(i)]))
                for i in order if scores[i] > 0.0]

    # ---- persistance -----------------------------------------------------------------
    def persist(self, directory: Optional[Path] = None, name: str = "tfidf_store.npz") -> Optional[Path]:
        """Sauvegarde le store dans un npz (vocab+docs en JSON) ; None si échec."""
        if self._matrix is None or self._idf is None:
            return None
        target = Path(directory) if directory else Path(get_settings().vector_store_dir)
        try:
            target.mkdir(parents=True, exist_ok=True)
            path = target / name
            np.savez(
                path,
                idf=self._idf,
                matrix=self._matrix,
                vocab_json=json.dumps(list(self._vocab)),
                docs_json=json.dumps([asdict(d) for d in self._docs], ensure_ascii=False),
            )
            return path
        except OSError:
            return None   # dispo en lecture seule : le service continue en mémoire

    def load(self, directory: Optional[Path] = None, name: str = "tfidf_store.npz") -> bool:
        """Recharge un store persisté ; False si absent ou corrompu."""
        target = Path(directory) if directory else Path(get_settings().vector_store_dir)
        path = target / name
        if not path.exists():
            return False
        try:
            with np.load(path, allow_pickle=False) as data:
                self._vocab = {t: i for i, t in enumerate(json.loads(str(data["vocab_json"])))}
                self._idf = data["idf"]
                self._matrix = data["matrix"]
                raw_docs = json.loads(str(data["docs_json"]))
                self._docs = [Document(content=d["content"], metadata=d.get("metadata", {}))
                              for d in raw_docs]
            return True
        except (OSError, ValueError, KeyError):
            return False
