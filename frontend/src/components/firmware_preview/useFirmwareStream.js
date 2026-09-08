/* useFirmwareStream — flux firmware [Flux.ai] via websocket_live.
 *
 * - Souscrit step_progress (étape 7 = Génération firmware) et export_ready ;
 *   FIRMWARE_REGENERATED pousse une nouvelle version d'un header.
 * - Rafraîchit via GET /exports/{id} (adapté : réponse { firmware: { files } }).
 * - Historique des versions par fichier → diff entre deux versions (diff.js).
 * - Repli « démo » : headers Zephyr/Arduino embarqués si gateway absente.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../services/api.js';
import { useLiveEvents, EVENT_TYPES } from '../../services/websocket_live/index.js';
import { SAMPLE_ZEPHYR_V1, SAMPLE_ZEPHYR_V2, SAMPLE_ARDUINO_V1 } from './samples.js';

const DEMO_ZEPHYR_FILE = 'board_config.h';
const DEMO_ARDUINO_FILE = 'board_config_arduino.h';

const EMPTY_STATE = { files: [], history: {} };

function nowTs() {
  return Date.now();
}

/** Fusion pure : liste de fichiers firmware → { files, history } mis à jour. */
function mergeFirmware(state, firmwareFiles) {
  const nextFiles = [...state.files];
  const nextHistory = { ...state.history };
  for (const file of firmwareFiles) {
    if (!file || !file.filename || typeof file.code !== 'string') continue;
    const versions = nextHistory[file.filename] || [];
    const last = versions[versions.length - 1];
    if (!last || last.code !== file.code) {
      nextHistory[file.filename] = [
        ...versions,
        { version: versions.length + 1, code: file.code, ts: nowTs() },
      ];
    }
    const idx = nextFiles.findIndex((f) => f.filename === file.filename);
    const entry = {
      filename: file.filename,
      framework: file.framework || 'zephyr',
      code: file.code,
      version: (nextHistory[file.filename] || []).length,
    };
    if (idx >= 0) nextFiles[idx] = entry;
    else nextFiles.push(entry);
  }
  return { files: nextFiles, history: nextHistory };
}

/** Contenu de démonstration : 2 versions Zephyr + 1 Arduino (pour le diff). */
function demoState() {
  return mergeFirmware(EMPTY_STATE, [
    { filename: DEMO_ZEPHYR_FILE, framework: 'zephyr', code: SAMPLE_ZEPHYR_V1 },
    { filename: DEMO_ZEPHYR_FILE, framework: 'zephyr', code: SAMPLE_ZEPHYR_V2 },
    { filename: DEMO_ARDUINO_FILE, framework: 'arduino', code: SAMPLE_ARDUINO_V1 },
  ]);
}

/** Extrait les headers depuis GET /exports/{id} — plusieurs formes tolérées. */
function extractFirmware(payload) {
  const candidates = [
    payload && payload.firmware && payload.firmware.files,
    payload && payload.firmware_files,
    payload && payload.files,
  ];
  for (const list of candidates) {
    if (Array.isArray(list)) {
      const headers = list.filter(
        (f) =>
          f && typeof f.filename === 'string' && f.filename.endsWith('.h') && typeof f.code === 'string'
      );
      if (headers.length) return headers;
    }
  }
  return [];
}

export function useFirmwareStream(projectId) {
  const [data, setData] = useState(EMPTY_STATE);
  const [framework, setFramework] = useState('zephyr');
  const [synced, setSynced] = useState(false);
  const [source, setSource] = useState('vide'); // vide | backend | demo
  const [lastSyncTs, setLastSyncTs] = useState(null);
  const [error, setError] = useState(null);

  const { lastEvent } = useLiveEvents(projectId);
  const seenEventsRef = useRef(new Set());

  const applyIngest = useCallback((firmwareFiles) => {
    if (!firmwareFiles || !firmwareFiles.length) return;
    setData((prev) => mergeFirmware(prev, firmwareFiles));
    setSource('backend');
    setSynced(true);
    setLastSyncTs(nowTs());
  }, []);

  const loadDemoState = useCallback(() => {
    setData(demoState());
    setSource('demo');
    setSynced(false);
    setLastSyncTs(null);
  }, []);

  /** Rafraîchit depuis la gateway ; repli démo si injoignable ou vide. */
  const refresh = useCallback(async () => {
    if (!projectId) {
      loadDemoState();
      return;
    }
    try {
      const dataResponse = await api.getExports(projectId);
      const headers = extractFirmware(dataResponse);
      if (headers.length) {
        applyIngest(headers);
        setError(null);
      } else {
        loadDemoState();
      }
    } catch (err) {
      loadDemoState();
      setError(
        err instanceof ApiError
          ? `Exports indisponibles (${err.status || 'réseau'}) — aperçu de démonstration`
          : 'Exports indisponibles — aperçu de démonstration'
      );
    }
  }, [projectId, applyIngest, loadDemoState]);

  /* Chargement initial + à chaque changement de projet. */
  useEffect(() => {
    refresh();
  }, [refresh]);

  /* Événements live : étape 7, export_ready, firmware régénéré. */
  useEffect(() => {
    if (!lastEvent || !lastEvent.event_id) return;
    if (seenEventsRef.current.has(lastEvent.event_id)) return;
    seenEventsRef.current.add(lastEvent.event_id);
    const p = lastEvent.payload || {};

    if (lastEvent.type === EVENT_TYPES.STEP_PROGRESS && Number(p.step) === 7) {
      if (p.status === 'running' || p.status === 'started') {
        setSynced(false);
      } else if (p.status === 'done' || p.status === 'completed') {
        setSynced(true);
        refresh();
      }
    } else if (lastEvent.type === EVENT_TYPES.EXPORT_READY) {
      const headers = Array.isArray(p.files)
        ? p.files.filter((f) => f && typeof f.filename === 'string' && f.filename.endsWith('.h'))
        : [];
      if (headers.length) {
        applyIngest(headers);
      } else if (!p.kind || p.kind === 'firmware') {
        refresh();
      }
    } else if (lastEvent.type === EVENT_TYPES.FIRMWARE_REGENERATED) {
      const file = p.file || p;
      if (file && file.filename && typeof file.code === 'string') {
        applyIngest([{ filename: file.filename, framework: file.framework, code: file.code }]);
      }
    }
  }, [lastEvent, refresh, applyIngest]);

  return {
    files: data.files,
    history: data.history,
    framework,
    setFramework,
    synced,
    source,
    lastSyncTs,
    error,
    refresh,
  };
}
