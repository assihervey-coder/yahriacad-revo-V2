"""Tests du branchement Ollama dans le RAG (brique Siemens Fuse).

Un serveur HTTP minimal émule l'API native Ollama (``/api/tags``, ``/api/chat``)
sur un port éphémère : on valide le mode « ollama » de bout en bout, le fallback
extractif quand le serveur est muet, et l'absence totale d'appel réseau en mode
« local ». Aucun serveur Ollama réel n'est requis en CI.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from common.config import Settings
from backend.services.ai_engine.llm_orchestrator.rag_engine.chunker import Document
from backend.services.ai_engine.llm_orchestrator.rag_engine.indexer import TfidfIndex
from backend.services.ai_engine.llm_orchestrator.rag_engine.ollama_client import OllamaClient
from backend.services.ai_engine.llm_orchestrator.rag_engine.retriever import RagRetriever


# ---- serveur Ollama de test -------------------------------------------------------
class _FakeOllama(BaseHTTPRequestHandler):
    """Émulation minimale de l'API native Ollama."""

    def log_message(self, *_args):        # silence des logs de requêtes
        return

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                     # health-check /api/tags
        if self.path.startswith("/api/tags"):
            self._json({"models": [{"name": "llama3.1:8b"}, {"name": "bge-m3"}]})
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self):                    # /api/chat
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.startswith("/api/chat"):
            question = ""
            for message in payload.get("messages", []):
                if message.get("role") == "user":
                    question = message.get("content", "")
            self._json({"model": payload.get("model", "?"),
                        "message": {"role": "assistant",
                                    "content": f"Réponse LLM local à : {question[:40]} [ds_stm32 p.2]"},
                        "done": True})
        else:
            self._json({"error": "not found"}, status=404)


@pytest.fixture()
def ollama_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture()
def mini_index():
    docs = [
        Document(content="Le MCU STM32F407 dissipe 0.9 W en charge nominale.",
                 metadata={"source": "ds_stm32", "page": 2, "section": "electrical"}),
        Document(content="Le régulateur buck L6981 supporte 2 A en sortie continue.",
                 metadata={"source": "ds_l6981", "page": 5, "section": "features"}),
        Document(content="Impédance différentielle cible USB2 : 90 ohms.",
                 metadata={"source": "ds_usb", "page": 12, "section": "layout"}),
    ]
    index = TfidfIndex()
    index.upsert(docs)
    return index


def _settings(provider: str, base_url: str) -> Settings:
    return Settings(llm_provider=provider, llm_model="llama3.1:8b",
                    ollama_base_url=base_url, ollama_timeout_s=2.0)


# ---- client Ollama -------------------------------------------------------------------
def test_client_health_and_models(ollama_url):
    client = OllamaClient(base_url=ollama_url, model="llama3.1:8b")
    assert client.is_available() is True
    assert "llama3.1:8b" in client.list_models()
    status = client.status()
    assert status["available"] is True and status["base_url"] == ollama_url


def test_client_chat_returns_content(ollama_url):
    client = OllamaClient(base_url=ollama_url, model="llama3.1:8b")
    reply = client.chat([{"role": "user", "content": "Quelle couche pour l'USB ?"}])
    assert reply is not None and "[ds_stm32 p.2]" in reply


def test_client_unreachable_returns_none():
    client = OllamaClient(base_url="http://127.0.0.1:1", model="x", timeout_s=0.3)
    assert client.is_available() is False
    assert client.list_models() == []
    assert client.chat([{"role": "user", "content": "test"}]) is None


# ---- RAG de bout en bout --------------------------------------------------------------
def test_rag_ollama_mode_end_to_end(ollama_url, mini_index):
    retriever = RagRetriever(index=mini_index, settings=_settings("ollama", ollama_url))
    result = retriever.answer("Quelle est la dissipation du STM32F407 ?")
    assert result["mode"] == "ollama"
    assert "[ds_stm32 p.2]" in str(result["answer"])
    assert any("ds_stm32" in str(c) for c in result["citations"])


def test_rag_fallback_when_ollama_down(mini_index):
    retriever = RagRetriever(index=mini_index,
                             settings=_settings("ollama", "http://127.0.0.1:1"))
    result = retriever.answer("Quelle est la dissipation du STM32F407 ?")
    assert result["mode"] == "extractif"            # fallback déterministe
    assert "STM32F407" in str(result["answer"])     # le contenu ancré reste servi


def test_rag_local_mode_makes_no_network_call(mini_index):
    retriever = RagRetriever(index=mini_index,
                             settings=_settings("local", "http://127.0.0.1:1"))
    result = retriever.answer("impédance USB2 ?")
    assert result["mode"] == "extractif"
    assert "90 ohms" in str(result["answer"])


def test_rag_ollama_empty_index_falls_back(ollama_url):
    retriever = RagRetriever(index=TfidfIndex(), settings=_settings("ollama", ollama_url))
    result = retriever.answer("question sans index")
    assert result["mode"] == "extractif"
