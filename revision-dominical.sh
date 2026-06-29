#!/bin/bash
# Wrapper de voz para la revisión dominical interactiva.
# Mismo patrón que voz-claude.sh pero conducido por Claude como entrevistador.
# Keybinding: Super+Shift+R
# Primera pulsación: Claude saluda y hace la primera pregunta.
# Pulsaciones siguientes: graba respuesta → transcribe → siguiente pregunta.
# Al terminar la revisión, Claude llama submit_revision y limpia la sesión.

AGENT="$HOME/bin/revision-dominical.py"
SESSION_FILE="$HOME/.local/share/voz-claude/revision.session"
HISTORY="$HOME/.local/share/voz-claude/revision-history.json"
AUDIO_FILE="/tmp/revision-input.wav"
WHISPER_SOCK="$HOME/.local/share/voz-claude/whisper.sock"
TTS_SOCK="$HOME/.local/share/voz-claude/tts.sock"
PIPER="/usr/bin/piper-tts"
PIPER_MODEL="$HOME/.local/share/piper/es_ES-davefx-medium.onnx"

LOCK="/tmp/revision.lock"
SPK="/tmp/revision-speaking.pid"
THINKING="/tmp/revision-thinking.pid"
NOTIF_FILE="/tmp/revision-notif.id"

hablar() {
    (
        if [ -S "$TTS_SOCK" ]; then
            printf '%s' "$1" | python3 -c "
import socket, sys, os
text = sys.stdin.buffer.read()
s = socket.socket(socket.AF_UNIX)
s.connect('$TTS_SOCK')
s.sendall(text)
s.shutdown(socket.SHUT_WR)
s.recv(16)
s.close()
" 2>/dev/null
        else
            printf '%s' "$1" | "$PIPER" --model "$PIPER_MODEL" --output_raw 2>/dev/null \
                | sox -t raw -r 22050 -e signed -b 16 -c 1 - -t raw -r 22050 -e signed -b 16 -c 1 - \
                    pitch -60 tempo 1.06 gain -1 2>/dev/null \
                | aplay -r 22050 -f S16_LE -t raw - 2>/dev/null
        fi
    ) &
    local PID=$!
    echo "$PID" > "$SPK"
    wait "$PID"
    rm -f "$SPK"
}

interrumpir() {
    local PID
    PID=$(cat "$SPK" 2>/dev/null)
    [ -n "$PID" ] && { pkill -P "$PID" 2>/dev/null; kill "$PID" 2>/dev/null; }
    PID=$(cat "$THINKING" 2>/dev/null)
    [ -n "$PID" ] && { pkill -P "$PID" 2>/dev/null; kill "$PID" 2>/dev/null; }
    pkill -f "revision-dominical.py" 2>/dev/null
    rm -f "$SPK" "$THINKING"
}

notif() {
    local ID
    ID=$(cat "$NOTIF_FILE" 2>/dev/null)
    if [ -n "$ID" ]; then
        notify-send -r "$ID" -t 60000 "Revisión" "$1" -u low 2>/dev/null
    else
        local NEW
        NEW=$(notify-send -p -t 60000 "Revisión" "$1" -u low 2>/dev/null)
        [ -n "$NEW" ] && echo "$NEW" > "$NOTIF_FILE"
    fi
}

notif_clear() {
    local ID
    ID=$(cat "$NOTIF_FILE" 2>/dev/null)
    [ -n "$ID" ] && makoctl dismiss -n "$ID" 2>/dev/null
    rm -f "$NOTIF_FILE"
}

beep() {
    sox -n -t raw -r 22050 -e signed -b 16 -c 1 - synth 0.07 sine 660 gain -8 2>/dev/null \
        | aplay -r 22050 -f S16_LE -t raw - 2>/dev/null
}

transcribir() {
    local ARCHIVO="$1"
    local TEXTO
    if [ -S "$WHISPER_SOCK" ]; then
        TEXTO=$(python3 -c "
import socket, sys
with socket.socket(socket.AF_UNIX) as s:
    s.connect('$WHISPER_SOCK')
    s.sendall(b'$ARCHIVO')
    s.shutdown(socket.SHUT_WR)
    data = b''
    while True:
        chunk = s.recv(4096)
        if not chunk: break
        data += chunk
    print(data.decode().strip())
" 2>/dev/null | tr -d '\n' | xargs)
    else
        whisper-ctranslate2 "$ARCHIVO" \
            --model base --language Spanish \
            --output_format txt --output_dir /tmp \
            --compute_type int8 2>/dev/null
        TEXTO=$(cat "${ARCHIVO%.wav}.txt" 2>/dev/null | tr -d '\n' | xargs)
    fi
    printf '%s' "$TEXTO"
}

hablar_y_procesar() {
    local TEXTO="$1"
    local SESSION="$2"
    local FIFO
    FIFO=$(mktemp -u /tmp/revision-fifo.XXXXXX)
    mkfifo "$FIFO"

    printf '%s\n' "$TEXTO" | python3 "$AGENT" "$SESSION" > "$FIFO" &
    local BPID=$!
    echo "$BPID" > "$THINKING"
    notif "Pensando..."

    while IFS= read -r frase; do
        notif "Respondiendo..."
        hablar "$frase"
    done < "$FIFO"

    wait "$BPID"
    rm -f "$FIFO" "$THINKING"
}

# ─── Punto de entrada ─────────────────────────────────────────────────────────

trap 'rm -f "${AUDIO_FILE%.wav}.txt" "$LOCK" "$THINKING"; notif_clear' EXIT

# Grabando → parar
if [ -f "$LOCK" ]; then
    _PID=$(cat "$LOCK" 2>/dev/null)
    [ -n "$_PID" ] && kill "$_PID" 2>/dev/null
    exit 0
fi

# Hablando o pensando → interrumpir
if [ -f "$SPK" ] || [ -f "$THINKING" ]; then
    interrumpir
    exit 0
fi

mkdir -p "$(dirname "$SESSION_FILE")"

# ── Primera vez: Claude arranca con el saludo ──────────────────────────────────
if [ ! -f "$HISTORY" ]; then
    beep
    notif "Iniciando revisión dominical..."
    hablar_y_procesar "" "$SESSION_FILE"
    notif "Presiona para responder"
    exit 0
fi

# ── Sesión activa: grabar respuesta del usuario ───────────────────────────────
beep
notif "Escuchando..."
timeout 120 pw-record --rate 16000 --channels 1 --format s16 "$AUDIO_FILE" 2>/dev/null &
REC=$!
echo "$REC" > "$LOCK"
wait "$REC"
rm -f "$LOCK"

[ ! -s "$AUDIO_FILE" ] && exit 0

notif "Transcribiendo..."
TEXTO=$(transcribir "$AUDIO_FILE")
rm -f "$AUDIO_FILE"

if [ -z "$TEXTO" ]; then
    hablar "No te escuché. Intenta de nuevo."
    exit 0
fi

notif "Pensando... ($TEXTO)"
hablar_y_procesar "$TEXTO" "$SESSION_FILE"

# Si la sesión terminó (Claude limpió el history), notificar
if [ ! -f "$HISTORY" ]; then
    notif_clear
else
    notif "Presiona para responder"
fi
