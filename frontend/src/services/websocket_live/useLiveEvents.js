/* Hook React d'abonnement au flux temps réel.
 *
 * useLiveEvents(projectId) → { events, lastEvent, connected, status,
 *                              latencyMs, resyncCount }
 *
 * Une seule WebSocket est ouverte par projet (store partagé) ; les composants
 * se synchronisent via useSyncExternalStore — rendu cohérent avec StrictMode.
 */

import { useCallback, useSyncExternalStore } from 'react';
import { liveStore } from './store.js';

export function useLiveEvents(projectId) {
  const pid = projectId || '';
  const subscribe = useCallback((cb) => liveStore.subscribe(pid, cb), [pid]);
  const getSnapshot = useCallback(() => liveStore.getSnapshot(pid), [pid]);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
