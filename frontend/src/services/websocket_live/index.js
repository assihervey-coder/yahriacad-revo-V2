/* Barrel du service websocket_live — routage temps réel des événements. */

export {
  EVENT_TYPES,
  EVENT_DESCRIPTIONS,
  WORKFLOW_STEPS,
  isLiveEvent,
  prettyLabel,
  stepLabel,
} from './events.js';
export { LiveSocket, LATENCY_TARGET_MS } from './socket.js';
export { liveStore, IDLE_SNAPSHOT } from './store.js';
export { useLiveEvents } from './useLiveEvents.js';
