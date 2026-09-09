"""Couche transport du plugin KiCad — WebSocket temps réel + IPC fichiers en repli.

Deux transports interchangeables derrière l'interface `Transport` :
  - `WebSocketClient` : ws://gateway/ws — bibliothèque `websockets` importée
    GARDÉE (jamais au moment de l'import du module) ; thread daemon avec sa
    propre boucle asyncio, reconnexion exponentielle, mesure de latence
    ping/pong comparée à la cible de 100 ms ;
  - `FileIPC` : dossier d'échange JSONL (out.jsonl / in.jsonl) pour le mode
    local mono-machine (plugin lancé dans le même conteneur que la plateforme).

Aucun appel réseau à l'import : les connexions sont explicites (`start()`).
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from common.config import get_settings
from common.log import get_logger

logger = get_logger("pcb_plugin.ipc")

DEFAULT_WS_URL = "ws://localhost:{port}/ws"
BACKOFF_BASE_S = 0.5
BACKOFF_MAX_S = 15.0


@dataclass
class TransportStats:
    """Compteurs et latence mesurée d'un transport (diagnostic plugin)."""

    sent: int = 0
    received: int = 0
    reconnects: int = 0
    errors: int = 0
    last_latency_ms: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    _latencies: List[float] = field(default_factory=list, repr=False)

    def record_latency(self, ms: float) -> None:
        self.last_latency_ms = ms
        self._latencies.append(ms)
        if len(self._latencies) > 100:
            self._latencies = self._latencies[-100:]
        self.avg_latency_ms = sum(self._latencies) / len(self._latencies)

    def to_dict(self) -> dict:
        return {
            "sent": self.sent, "received": self.received,
            "reconnects": self.reconnects, "errors": self.errors,
            "last_latency_ms": round(self.last_latency_ms, 2) if self.last_latency_ms else None,
            "avg_latency_ms": round(self.avg_latency_ms, 2) if self.avg_latency_ms else None,
        }


class Transport:
    """Interface commune — API synchrone, thread-safe usage mono-éditeur."""

    def start(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def send(self, message: dict) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def poll(self, timeout_s: float = 0.0) -> Optional[dict]:  # pragma: no cover
        raise NotImplementedError

    @property
    def stats(self) -> TransportStats:  # pragma: no cover - interface
        raise NotImplementedError

    @property
    def connected(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class WebSocketClient(Transport):
    """Client WebSocket vers la passerelle — reconnexion exponentielle.

    La bibliothèque `websockets` est importée dans `start()` (import gardé) :
    si elle est absente, `start()` retourne False et la fabrique
    `create_transport` bascule sur FileIPC. La latence est mesurée par
    ping/pong applicatif toutes les `ping_interval_s` et comparée à la cible
    `websocket_target_latency_ms` (100 ms) — un dépassement est journalisé.
    """

    def __init__(self, url: str, target_latency_ms: Optional[int] = None,
                 ping_interval_s: float = 5.0) -> None:
        self.url = url
        self.target_latency_ms = target_latency_ms or get_settings().websocket_target_latency_ms
        self.ping_interval_s = ping_interval_s
        self._stats = TransportStats()
        self._connected = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._inbox: "asyncio.Queue[dict]" = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- API synchrone --------------------------------------------------------
    def start(self) -> bool:
        try:
            import websockets  # noqa: F401 — import gardé (voir docstring)
        except ImportError:
            logger.warning("websockets indisponible — transport WebSocket inutilisable")
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="kicad_ws", daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def send(self, message: dict) -> bool:
        """Dépose un message sur la file d'émission (traité par la boucle asyncio)."""
        if self._loop is None or not self._connected:
            return False
        asyncio.run_coroutine_threadsafe(self._outbox.put(message), self._loop)
        self._stats.sent += 1
        return True

    def poll(self, timeout_s: float = 0.0) -> Optional[dict]:
        try:
            if self._loop is None:
                return None
            fut = asyncio.run_coroutine_threadsafe(self._inbox.get(), self._loop)
            return fut.result(timeout=timeout_s) if timeout_s > 0 else fut.result(timeout=0.01)
        except Exception:
            return None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> TransportStats:
        """Statistiques du transport (latence, erreurs, reconnexions)."""
        return self._stats

    # -- boucle asyncio interne -----------------------------------------------
    async def _session(self) -> None:
        import websockets  # import gardé — garanti présent après start()

        backoff = BACKOFF_BASE_S
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, open_timeout=3.0) as ws:
                    self._connected = True
                    self._stats.reconnects += 1 if backoff < BACKOFF_MAX_S else 0
                    backoff = BACKOFF_BASE_S
                    logger.info("websocket connecté", extra={"url": self.url})
                    await self._pump(ws)
            except Exception as exc:
                self._connected = False
                self._stats.errors += 1
                if self._stop.is_set():
                    break
                jitter = backoff * (0.8 + 0.4 * random.random())
                logger.warning("websocket déconnecté — reconnexion programmée",
                               extra={"url": self.url, "backoff_s": round(jitter, 2),
                                      "error": str(exc)})
                await asyncio.sleep(jitter)
                backoff = min(BACKOFF_MAX_S, backoff * 2)

    async def _pump(self, ws) -> None:
        """Pompe bidirectionnelle : messages entrants + pings de latence."""
        last_ping = time.perf_counter()
        while not self._stop.is_set():
            recv_task = asyncio.ensure_future(ws.recv())
            sleep_task = asyncio.ensure_future(asyncio.sleep(0.05))
            done, pending = await asyncio.wait(
                {recv_task, sleep_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if recv_task in done:
                try:
                    raw = recv_task.result()
                    self._inbox.put_nowait(json.loads(raw))
                    self._stats.received += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # payload corrompu — ignoré mais compté
                    self._stats.errors += 1
                    logger.warning("message websocket illisible", extra={"error": str(exc)})
            now = time.perf_counter()
            if now - last_ping >= self.ping_interval_s:
                last_ping = now
                latency_ms = (await self._measure_latency(ws)) * 1000.0
                self._stats.record_latency(latency_ms)
                if latency_ms > self.target_latency_ms:
                    logger.warning(
                        "latence websocket au-dessus de la cible",
                        extra={"latency_ms": round(latency_ms, 1),
                               "target_ms": self.target_latency_ms},
                    )

    @staticmethod
    async def _measure_latency(ws) -> float:
        """Aller-retour d'une trame ping/pong applicative (secondes)."""
        t0 = time.perf_counter()
        pong = await ws.ping()
        await asyncio.wait_for(pong, timeout=2.0)
        return time.perf_counter() - t0

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session())
        finally:
            self._loop.close()
            self._loop = None


class FileIPC(Transport):
    """IPC fichiers JSONL — mode local mono-machine (zéro dépendance réseau).

    Convention de dossier d'échange :
      out.jsonl : plateforme → plugin (segments à appliquer, acks moteur)
      in.jsonl  : plugin → plateforme (acks, événements d'édition)
    Chaque transport écrit dans `out` et lit `in` avec un curseur d'octets —
    deux processus peuvent partager le même dossier sans se marcher dessus.
    """

    def __init__(self, exchange_dir: Path, suffix: str = "") -> None:
        self.dir = Path(exchange_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        tag = f".{suffix}" if suffix else ""
        self.out_path = self.dir / f"out{tag}.jsonl"
        self.in_path = self.dir / f"in{tag}.jsonl"
        self.out_path.touch(exist_ok=True)
        self.in_path.touch(exist_ok=True)
        self._cursor = 0
        self._stats = TransportStats()
        self._lock = threading.Lock()
        self._closed = False

    def start(self) -> bool:
        return True  # rien à ouvrir — fichiers manipulés à chaque appel

    def stop(self) -> None:
        self._closed = True

    @property
    def connected(self) -> bool:
        return not self._closed

    @property
    def stats(self) -> TransportStats:
        """Statistiques du transport fichier (envois, réceptions, erreurs)."""
        return self._stats

    def send(self, message: dict) -> bool:
        try:
            with self._lock:
                with open(self.out_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._stats.sent += 1
            return True
        except OSError as exc:
            self._stats.errors += 1
            logger.warning("écriture IPC fichier échouée", extra={"error": str(exc)})
            return False

    def poll(self, timeout_s: float = 0.0) -> Optional[dict]:
        """Lit le message suivant de in.jsonl (curseur persistant) — None si rien."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            message = self._read_next()
            if message is not None:
                return message
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))

    def _read_next(self) -> Optional[dict]:
        try:
            size = self.in_path.stat().st_size
        except OSError:
            return None
        if size <= self._cursor:
            return None
        with self._lock:
            with open(self.in_path, "rb") as fh:
                fh.seek(self._cursor)
                data = fh.read()
            # ne consomme que des lignes complètes (écrivain non tronqué)
            nl = data.rfind(b"\n")
            if nl < 0:
                return None
            chunk = data[:nl]
            self._cursor += nl + 1
        for line in chunk.split(b"\n"):
            line = line.strip()
            if not line:
                continue
            try:
                self._stats.received += 1
                return json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._stats.errors += 1
                logger.warning("ligne IPC illisible — ignorée")
        return None


def create_transport(config: Optional[Dict] = None) -> Transport:
    """Fabrique de transport : 'ws' si URL configurée et websockets présent, sinon FileIPC.

    config : {"mode": "auto"|"ws"|"file", "url": str, "dir": str, "suffix": str}
    Aucun appel réseau ici — la connexion n'a lieu qu'au `start()`.
    """
    config = config or {}
    mode = config.get("mode", "auto")
    settings = get_settings("pcb_plugin")
    url = config.get("url") or DEFAULT_WS_URL.format(port=settings.gateway_port)
    exchange_dir = Path(config.get("dir", os.environ.get("PCB_IPC_DIR", "/tmp/pcb_ipc")))
    suffix = config.get("suffix", "")

    if mode in ("ws", "auto"):
        client = WebSocketClient(url)
        if mode == "ws":
            return client
        # auto : WebSocket seulement si la librairie est réellement importable
        try:
            import websockets  # noqa: F401 — sondage d'import gardé
            return client
        except ImportError:
            logger.info("websockets absent — repli IPC fichiers (mode local)")
    return FileIPC(exchange_dir, suffix=suffix)
