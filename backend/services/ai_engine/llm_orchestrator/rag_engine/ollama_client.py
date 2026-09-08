"""Client Ollama natif — le LLM local réellement branché dans le RAG.

Contrairement à l'adaptateur générique « OpenAI-compatible » (retriever._llm_answer,
qui cible ``/v1/chat/completions``), ce client parle l'API **native** d'Ollama :

  * ``GET  /api/tags``         — liste des modèles installés (health-check gratuit) ;
  * ``POST /api/chat``         — génération conversationnelle (stream=False) ;
  * ``POST /api/generate``     — génération brute par prompt ;
  * ``POST /api/embeddings``   — vecteurs d'embedding du modèle embarqué.

Garanties héritées des conventions du dépôt :
  * httpx reste un import gardé — le module s'importe sans réseau ni dépendance ;
  * toute erreur (serveur arrêté, timeout, JSON invalide) est convertie en
    ``None``/``[]`` + log warning : le RAG retombe alors sur le mode extractif
    déterministe, jamais sur une exception ;
  * aucun appel réseau à l'import — le client ne contacte Ollama qu'à la
    première méthode appelée explicitement.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

from common.config import Settings, get_settings
from common.log import get_logger

logger = get_logger("ai_engine.rag.ollama")


class OllamaClient:
    """Passerelle vers un serveur Ollama local (http://localhost:11434 par défaut).

    Exemple :
        >>> client = OllamaClient(model="llama3.1:8b")
        >>> client.is_available()
        True
        >>> client.chat([{"role": "user", "content": "Liste les couches d'une carte 4 cuivres."}])
        'Une carte 4 cuivres empile F.Cu, GND, PWR et B.Cu.'
    """

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None,
                 timeout_s: Optional[float] = None, num_ctx: Optional[int] = None,
                 settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings("ai_engine")
        self.base_url = (base_url or self.settings.ollama_base_url).rstrip("/")
        self.model = model or self.settings.llm_model
        self.timeout_s = float(timeout_s or self.settings.ollama_timeout_s)
        self.num_ctx = int(num_ctx or self.settings.ollama_num_ctx)
        self.last_latency_ms: Optional[float] = None      # diagnostic / Prometheus

    # ---- infrastructure --------------------------------------------------------------
    def _post(self, path: str, payload: Dict[str, Any],
              timeout_s: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """POST JSON vers l'API native — None si httpx absent ou serveur muet."""
        try:
            import httpx   # import gardé — dépendance optionnelle à l'exécution
        except ImportError:
            logger.warning("httpx absent — client Ollama inopérant (pip install httpx)")
            return None
        started = time.perf_counter()
        try:
            response = httpx.post(f"{self.base_url}{path}", json=payload,
                                  timeout=timeout_s or self.timeout_s)
            response.raise_for_status()
            self.last_latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
            return response.json()
        except Exception as exc:   # connexion refusée, timeout, HTTP 5xx, JSON
            logger.warning("Ollama injoignable (%s%s) — fallback déterministe",
                           self.base_url, path, extra={"error": str(exc)[:160]})
            return None

    def _get(self, path: str, timeout_s: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """GET JSON vers l'API native — None si indisponible."""
        try:
            import httpx
        except ImportError:
            return None
        try:
            response = httpx.get(f"{self.base_url}{path}",
                                 timeout=timeout_s or self.timeout_s)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning("Ollama injoignable (GET %s) — %s", self.base_url, str(exc)[:120])
            return None

    # ---- santé / découverte --------------------------------------------------------------
    def is_available(self) -> bool:
        """True si le serveur répond au health-check ``/api/tags``."""
        return self._get("/api/tags", timeout_s=min(2.0, self.timeout_s)) is not None

    def list_models(self) -> List[str]:
        """Noms des modèles installés (``ollama list`` côté serveur)."""
        data = self._get("/api/tags", timeout_s=min(2.0, self.timeout_s))
        if not data:
            return []
        return [str(entry.get("name", "")) for entry in data.get("models", []) if entry.get("name")]

    def status(self) -> Dict[str, Any]:
        """Diagnostic complet — injecté dans les logs de démarrage du service."""
        models = self.list_models()
        return {"base_url": self.base_url, "model": self.model,
                "available": bool(models), "models": models,
                "timeout_s": self.timeout_s, "num_ctx": self.num_ctx}

    # ---- génération --------------------------------------------------------------
    def chat(self, messages: Sequence[Dict[str, str]],
             temperature: float = 0.2, stream: bool = False) -> Optional[str]:
        """Génération conversationnelle — contenu texte, ou None si échec.

        ``stream=False`` impose une réponse JSON unique (le RAG n'a pas besoin
        de streaming server-side ; la latence cible est portée par le WebSocket
        du frontend, pas par le token-streaming du LLM).
        """
        payload = {"model": self.model, "messages": list(messages),
                   "stream": stream, "options": {"num_ctx": self.num_ctx,
                                                 "temperature": temperature}}
        data = self._post("/api/chat", payload)
        if not data:
            return None
        message = data.get("message") or {}
        content = str(message.get("content", "")).strip()
        return content or None

    def generate(self, prompt: str, system: Optional[str] = None,
                 temperature: float = 0.2) -> Optional[str]:
        """Génération brute (``/api/generate``) — utile pour les one-shots."""
        payload: Dict[str, Any] = {"model": self.model, "prompt": prompt,
                                   "stream": False,
                                   "options": {"num_ctx": self.num_ctx,
                                               "temperature": temperature}}
        if system:
            payload["system"] = system
        data = self._post("/api/generate", payload)
        if not data:
            return None
        return str(data.get("response", "")).strip() or None

    def embeddings(self, text: str) -> Optional[List[float]]:
        """Vecteur d'embedding du modèle serveur — None si échec.

        Utilisé en appoint de l'index TF-IDF : quand Ollama sert un modèle
        d'embedding (ex. ``bge-m3``, cf. ``EMBEDDING_MODEL``), le retriever
        peut affiner le classement des passages par similarité cosinus dense.
        """
        data = self._post("/api/embeddings",
                          {"model": self.settings.embedding_model, "prompt": text})
        if not data:
            return None
        vector = data.get("embedding")
        return [float(x) for x in vector] if isinstance(vector, list) else None
