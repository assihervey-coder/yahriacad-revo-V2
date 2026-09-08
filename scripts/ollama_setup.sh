#!/usr/bin/env bash
# =============================================================================
# ollama_setup.sh — installe le LLM local du RAG (défaut : qwen2.5-coder:14b)
#
# Détecte automatiquement la cible :
#   1. binaire ollama natif (https://ollama.com) ;
#   2. service docker compose du dépôt (`docker compose up -d ollama`).
#
# Vérifie ensuite le GPU (nvidia-smi) et conseille un modèle adapté à la VRAM.
# Variables : OLLAMA_MODEL (défaut qwen2.5-coder:14b), OLLAMA_BASE_URL.
# =============================================================================
set -euo pipefail

MODEL="${OLLAMA_MODEL:-qwen2.5-coder:14b}"
BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
VRAM_MIN_GB=10          # qwen2.5-coder:14b q4_K_M ≈ 9 Go de poids + KV cache
FALLBACK_MODEL="llama3.1:8b"

log()  { printf '[ollama-setup] %s\n' "$*"; }
warn() { printf '[ollama-setup] AVERTISSEMENT : %s\n' "$*" >&2; }

# --- 1) GPU ? ----------------------------------------------------------------
if command -v nvidia-smi >/dev/null 2>&1; then
    VRAM_MIB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | sort -n | tail -1)
    VRAM_GB=$(( VRAM_MIB / 1024 ))
    log "GPU détecté : ${VRAM_GB} Go de VRAM"
    if [ "${VRAM_GB}" -lt "${VRAM_MIN_GB}" ]; then
        warn "${VRAM_GB} Go < ${VRAM_MIN_GB} Go : ${MODEL} ne tiendra pas entièrement "
        warn "en VRAM (offload CPU partiel = lenteur). Modèle de repli conseillé : ${FALLBACK_MODEL}"
    fi
else
    warn "pas de GPU NVIDIA détecté — inférence CPU (lente pour un 14B)."
    warn "Pour le GPU en compose : docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d ollama"
fi

# --- 2) cible : natif ou compose ? --------------------------------------------
OLLAMA_CMD=()
if command -v ollama >/dev/null 2>&1; then
    OLLAMA_CMD=(ollama)
    log "binaire natif détecté : $(command -v ollama)"
elif docker compose ps ollama 2>/dev/null | grep -q ollama; then
    OLLAMA_CMD=(docker compose exec -T ollama ollama)
    log "service compose 'ollama' détecté"
else
    log "démarrage du service compose 'ollama'…"
    docker compose up -d ollama
    OLLAMA_CMD=(docker compose exec -T ollama ollama)
fi

# --- 3) pull du modèle ----------------------------------------------------------
log "pull de ${MODEL} (~9 Go en q4_K_M — patientez)…"
"${OLLAMA_CMD[@]}" pull "${MODEL}"

# --- 4) vérification via l'API /api/tags ------------------------------------------
sleep 1
if command -v curl >/dev/null 2>&1; then
    if curl -fsS "${BASE_URL}/api/tags" | grep -q "${MODEL}"; then
        log "OK — ${MODEL} servi par ${BASE_URL} (RAG prêt : LLM_PROVIDER=ollama)"
    else
        warn "${MODEL} introuvable via ${BASE_URL}/api/tags — vérifiez le serveur."
        exit 1
    fi
fi

log "Terminé. Côté plateforme : LLM_PROVIDER=ollama LLM_MODEL=${MODEL} make start-services"
