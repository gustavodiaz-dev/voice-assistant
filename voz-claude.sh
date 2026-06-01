#!/bin/bash

AUDIO_FILE="/tmp/voz-claude-input.wav"
PIPER="/usr/bin/piper-tts"
PIPER_MODEL="$HOME/.local/share/piper/es_ES-davefx-medium.onnx"
WHISPER_SOCK="$HOME/.local/share/voz-claude/whisper.sock"
SESSION_FILE="$HOME/.local/share/voz-claude-session.id"
BRIDGE="$HOME/bin/claude-tts-bridge.py"

LOCK="/tmp/voz-claude.lock"
SPK="/tmp/voz-claude-speaking.pid"
THINKING="/tmp/voz-claude-thinking.pid"
ULTIMA="/tmp/voz-claude-ultima.txt"
NOTIF_FILE="/tmp/voz-claude-notif.id"

NADA=("No entendí nada." "No te escuché bien." "Repite, por favor." "No capté nada.")

hablar() {
    (
        printf '%s' "$1" | "$PIPER" --model "$PIPER_MODEL" --output_raw 2>/dev/null \
            | sox -t raw -r 22050 -e signed -b 16 -c 1 - -t raw -r 22050 -e signed -b 16 -c 1 - \
                pitch -60 tempo 1.06 \
                gain -1 2>/dev/null \
            | aplay -r 22050 -f S16_LE -t raw - 2>/dev/null
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
    pkill -f "claude-tts-bridge.py" 2>/dev/null
    pkill -f "claude --print" 2>/dev/null
    rm -f "$SPK" "$THINKING"
}

notif() {
    local ID
    ID=$(cat "$NOTIF_FILE" 2>/dev/null)
    if [ -n "$ID" ]; then
        notify-send -r "$ID" -t 30000 "Voz" "$1" -u low 2>/dev/null
    else
        local NEW
        NEW=$(notify-send -p -t 30000 "Voz" "$1" -u low 2>/dev/null)
        [ -n "$NEW" ] && echo "$NEW" > "$NOTIF_FILE"
    fi
}

notif_clear() {
    local ID
    ID=$(cat "$NOTIF_FILE" 2>/dev/null)
    [ -n "$ID" ] && makoctl dismiss -n "$ID" 2>/dev/null
    rm -f "$NOTIF_FILE"
}

aleatorio() { local arr=("$@"); echo "${arr[$((RANDOM % ${#arr[@]}))]}"; }

beep() {
    sox -n -t raw -r 22050 -e signed -b 16 -c 1 - synth 0.07 sine 880 gain -8 2>/dev/null \
        | aplay -r 22050 -f S16_LE -t raw - 2>/dev/null
}

# ─── Punto de entrada ────────────────────────────────────────────────────────

# Grabando → parar (continúa en la misma instancia del script)
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

# ─── Nueva interacción ───────────────────────────────────────────────────────

trap 'rm -f "$AUDIO_FILE" "${AUDIO_FILE%.wav}.txt" "$LOCK" "$THINKING"; notif_clear' EXIT

mkdir -p "$(dirname "$SESSION_FILE")"
beep

notif "Escuchando..."
timeout 60 pw-record --rate 16000 --channels 1 --format s16 "$AUDIO_FILE" 2>/dev/null &
REC=$!
echo "$REC" > "$LOCK"
wait "$REC"
rm -f "$LOCK"

[ ! -s "$AUDIO_FILE" ] && exit 0

notif "Transcribiendo..."
if [ -S "$WHISPER_SOCK" ]; then
    TEXTO=$(python3 -c "
import socket, sys
with socket.socket(socket.AF_UNIX) as s:
    s.connect('$WHISPER_SOCK')
    s.sendall(b'$AUDIO_FILE')
    s.shutdown(socket.SHUT_WR)
    data = b''
    while True:
        chunk = s.recv(4096)
        if not chunk: break
        data += chunk
    print(data.decode().strip())
" 2>/dev/null | tr -d '\n' | xargs)
else
    whisper-ctranslate2 "$AUDIO_FILE" \
        --model base --language Spanish \
        --output_format txt --output_dir /tmp \
        --compute_type int8 2>/dev/null
    TEXTO=$(cat "${AUDIO_FILE%.wav}.txt" 2>/dev/null | tr -d '\n' | xargs)
fi

if [ -z "$TEXTO" ]; then
    hablar "$(aleatorio "${NADA[@]}")"
    exit 0
fi

LOWER=$(printf '%s' "$TEXTO" | tr '[:upper:]' '[:lower:]')

# Comandos de voz
if echo "$LOWER" | grep -qE "nueva conversacion|nuevo tema|reinicia|borra el historial|empecemos de nuevo"; then
    rm -f "$SESSION_FILE"
    hablar "Conversación reiniciada."
    exit 0
fi

if echo "$LOWER" | grep -qE "repite|dilo otra vez|no te escuché|no te escuche|qué dijiste|que dijiste"; then
    ANT=$(cat "$ULTIMA" 2>/dev/null)
    hablar "${ANT:-No hay respuesta anterior.}"
    exit 0
fi

if echo "$LOWER" | grep -qE "copia eso|cópialo|copialo|ponlo en el portapapeles"; then
    ANT=$(cat "$ULTIMA" 2>/dev/null)
    if [ -n "$ANT" ]; then
        printf '%s' "$ANT" | wl-copy 2>/dev/null
        hablar "Copiado."
    else
        hablar "No hay nada que copiar."
    fi
    exit 0
fi

# ─── Claude con streaming ─────────────────────────────────────────────────────

notif "Pensando..."

SESSION_ID=$(cat "$SESSION_FILE" 2>/dev/null)

if [ -z "$SESSION_ID" ]; then
    PROMPT="Eres un asistente de voz. Responde SIEMPRE en español, de forma concisa y natural para una conversación hablada. Máximo 3 oraciones cortas.

Reglas de escritura natural (anti-IA):
- Sin guiones largos (—) ni en dash (–), nunca
- Sin palabras IA: crucial, delve, destacar, vibrant, tapestry, landscape, pivotal, showcase, underscore, foster, enhance
- Sin regla de tres forzada (listas de exactamente 3 cosas)
- Sin frases -ing vacías al final: «contribuyendo a», «reflejando», «destacando»
- Sin voz pasiva cuando puedes usar activa
- Sin «cabe señalar», «es importante mencionar», «vale la pena destacar»
- Varía el ritmo: mezcla oraciones cortas con largas
- Opina cuando sea natural, no solo reportes hechos

$TEXTO"
else
    PROMPT="$TEXTO"
fi

FIFO=$(mktemp -u /tmp/voz-fifo.XXXXXX)
mkfifo "$FIFO"

printf '%s\n' "$PROMPT" | claude --print \
    --output-format stream-json --verbose \
    ${SESSION_ID:+--resume "$SESSION_ID"} \
    --dangerously-skip-permissions 2>/dev/null \
    | python3 "$BRIDGE" "$SESSION_FILE" > "$FIFO" &

BPID=$!
echo "$BPID" > "$THINKING"

RESP=""
while IFS= read -r frase; do
    notif "Respondiendo..."
    hablar "$frase"
    RESP="$RESP$frase "
done < "$FIFO"

wait "$BPID"
rm -f "$FIFO"

RESP=$(printf '%s' "$RESP" | xargs)
[ -n "$RESP" ] && printf '%s' "$RESP" > "$ULTIMA"

notif_clear
