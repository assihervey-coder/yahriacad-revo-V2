"""Retriever RAG — passages pertinents + réponses « anchored » avec citations.

Deux modes : (1) extractif déterministe — les meilleures phrases des passages
retrouvés sont concaténées, chacune suffixée par sa citation ``[source p.X]`` ;
(2) LLM optionnel — un POST vers ``settings.llm_api_base`` (API compatible
OpenAI via httpx) est tenté UNIQUEMENT si ``llm_provider`` ≠ ``local`` et si
httpx est importable ; tout échec (réseau, JSON, timeout de 4 s) retombe
instantanément sur le mode extractif — le service ne dépend jamais du réseau.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from common.config import Settings, get_settings
from common.log import get_logger

from .chunker import Document
from .indexer import TfidfIndex

logger = get_logger("ai_engine.rag")

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_STOPWORDS = {"the", "a", "an", "of", "for", "and", "or", "to", "is", "are",
              "le", "la", "les", "de", "des", "du", "et", "ou", "un", "une"}


class RagRetriever:
    """Réponses ancrées sur les datasheets indexées — zéro hallucination nue."""

    def __init__(self, index: Optional[TfidfIndex] = None,
                 settings: Optional[Settings] = None) -> None:
        self.index = index or TfidfIndex()
        self.settings = settings or get_settings("ai_engine")

    # ---- retrieval ---------------------------------------------------------------
    def retrieve(self, query: str, k: int = 4) -> List[Tuple[Document, float]]:
        """Meilleurs passages (Document, score cosinus TF-IDF)."""
        return self.index.search(query, k=k)

    # ---- réponse -----------------------------------------------------------------
    def answer(self, query: str, k: int = 4) -> Dict[str, object]:
        """Réponse ancrée : texte + liste de citations + mode réellement utilisé."""
        passages = self.retrieve(query, k=k)
        citations = [doc.citation() for doc, _score in passages]
        if self.settings.llm_provider != "local":
            llm_text = self._llm_answer(query, passages)
            if llm_text is not None:
                return {"answer": llm_text, "citations": citations, "mode": "llm"}
        return {"answer": self._extractive_answer(query, passages),
                "citations": citations, "mode": "extractif"}

    def _extractive_answer(self, query: str, passages: List[Tuple[Document, float]]) -> str:
        """Concatène les meilleures phrases, chacune citée — déterministe."""
        if not passages:
            return ("Aucun passage pertinent dans l'index RAG : alimentez l'index "
                    "avec les datasheets du projet (rag_engine.indexer.TfidfIndex.upsert).")
        keywords = {w for w in re.findall(r"[a-zà-ÿ0-9]{2,}", query.lower())
                    if w not in _STOPWORDS}
        selected: List[str] = []
        for doc, _score in passages[:3]:
            sentences = _SENTENCE_RE.split(doc.content)
            scored = [(sum(1 for w in keywords if w in s.lower()), s)
                      for s in sentences]
            scored.sort(key=lambda pair: -pair[0])
            best = scored[0][1].strip() if scored and scored[0][0] > 0 else sentences[0].strip()
            if best:
                selected.append(f"{best} {doc.citation()}")
        return " ".join(selected)

    def _llm_answer(self, query: str, passages: List[Tuple[Document, float]]) -> str | None:
        """Adaptateur LLM optionnel — None = fallback immédiat sur le mode extractif."""
        try:
            import httpx   # import gardé — dépendance optionnelle à l'exécution
        except ImportError:
            return None
        context = "\n\n".join(
            f"{doc.citation()} {doc.content[:600]}" for doc, _s in passages[:4])
        prompt = (f"Réponds en français, chaque affirmation doit citer sa source "
                  f"[source p.X]. Contexte :\n{context}\n\nQuestion : {query}")
        try:
            response = httpx.post(
                f"{self.settings.llm_api_base.rstrip('/')}/chat/completions",
                json={"model": self.settings.llm_model,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=4.0,
            )
            response.raise_for_status()
            data = response.json()
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception as exc:   # réseau, JSON, timeout — fallback déterministe
            logger.warning("adaptateur LLM indisponible — réponse extractive",
                           extra={"error": str(exc)[:160]})
            return None
