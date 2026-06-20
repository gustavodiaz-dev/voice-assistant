#!/usr/bin/env python3
"""
Agente de voz con tool use via Anthropic SDK.
stdin   → texto transcrito del usuario
argv[1] → session file (marca si hay sesión activa)
argv[2] → rutas de imágenes separadas por espacio (opcional, para screen capture)
stdout  → oraciones limpias listas para TTS
"""
import base64
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-4-6"
HISTORY_FILE = Path.home() / ".local/share/voz-claude/history.json"
MAX_MESSAGES = 20

SYSTEM_PROMPT = """Eres un asistente de voz personal. Responde SIEMPRE en español, de forma concisa y natural para conversación hablada. Máximo 3 oraciones cortas salvo que el usuario pida más detalle.

Tienes herramientas para controlar el sistema y buscar información. Úsalas sin pedir permiso cuando sean seguras.

IMPORTANTE — operaciones destructivas: antes de ejecutar cualquier comando que borre, sobreescriba, cambie permisos, apague o modifique datos irreversiblemente, pregunta primero en voz alta y espera confirmación explícita en el siguiente turno. Ejemplos que SIEMPRE requieren confirmación: eliminar archivos o directorios, matar procesos, modificar archivos de sistema, formatear, desinstalar paquetes. Si el usuario no confirma claramente, no ejecutes.

Reglas de lenguaje:
- Sin guiones largos (—) ni (–)
- Sin listas con viñetas, habla en oraciones
- Sin palabras IA: crucial, destacar, vibrant, pivotal, tapestry
- Sin «cabe señalar», «es importante mencionar»
- Varía el ritmo: mezcla oraciones cortas con largas
- Opina cuando sea natural

Revisión dominical: cuando el usuario te pida hacer la revisión semanal o mensual (puede decir "vamos a hacer la revisión dominical", "toca revisión", etc.), guía la conversación preguntando por estos campos uno a la vez o en grupos naturales, sin sonar como un formulario. No preguntes por un campo si el usuario ya lo mencionó espontáneamente.

Campos de TODA revisión (semanal y mensual):
- semana: título corto de la revisión (ej. "Semana del 15 al 21 de junio")
- tipo: "🗓️ Semanal" o "📊 Mensual profunda" (mensual solo si el usuario lo indica o si es la última revisión del mes)
- energia: "🔋 Alta", "⚡ Media", "🪫 Baja" o "🛌 Agotado"
- ingles_avance: sesiones, lectura, output, vocabulario nuevo en inglés
- libro_paginas: número de páginas leídas del libro en inglés esta semana
- libro_vocabulario: palabras o expresiones nuevas extraídas del libro
- trabajo_nuevo_avance: avance en aplicaciones, entrevistas, materiales de búsqueda de empleo
- usa2028_avance: avance, bloqueos y próximo paso del proyecto USA 2028
- homelab_avance: avance técnico en el homelab, horas invertidas
- fitness_avance: avance en entrenamiento y alimentación esta semana (adherencia al plan, progresión de cargas, cómo se sintió)
- finanzas_avance: estado del plan financiero, deudas, ahorro
- universidad_avance: materia activa, entregables, próximos hitos
- decisiones_tomadas: decisiones importantes tomadas esta semana
- tres_acciones_top: las 3 acciones más importantes de la semana
- foco_proxima_semana: foco principal para la semana que viene
- horario_proxima_semana: bloques de horario para la próxima semana, cada uno con proyecto, día, hora de inicio y hora de fin

Campos SOLO de revisión mensual profunda (omite si es semanal):
- hitos_del_mes: hitos logrados en el mes
- ajustes_al_sistema: cambios al sistema, rutinas, presupuesto
- reflexion_profunda: alineación con la visión USA 2028, ánimo general del mes
- temperatura_mes: cómo sintió el mes en general
- logro_mes: el logro más importante del mes
- revision_sistema: qué tan bien está funcionando el sistema de productividad/seguimiento
- mes_siguiente_foco: foco para el próximo mes
- decision_postergada: alguna decisión que se está posponiendo

Cuando tengas todos los campos relevantes, usa run_shell con curl para hacer POST a https://tailscale.tail7636ea.ts.net/webhook/revision-dominical con un JSON body que tenga estos campos como llaves (horario_proxima_semana como array de objetos {proyecto, dia, inicio, fin}). Confirma en voz que la revisión quedó guardada."""

TOOLS = [
    {
        "name": "run_shell",
        "description": "Ejecuta un comando de shell. Para controlar el sistema, abrir programas, obtener información del sistema, manejar archivos, etc.",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    },
    {
        "name": "open_app",
        "description": "Abre una aplicación por nombre (firefox, alacritty, spotify, obsidian, code, etc.)",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {
        "name": "get_clipboard",
        "description": "Lee el contenido del portapapeles",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_clipboard",
        "description": "Escribe texto en el portapapeles",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "set_volume",
        "description": "Ajusta el volumen del sistema entre 0 y 100",
        "input_schema": {"type": "object", "properties": {"percent": {"type": "integer"}}, "required": ["percent"]},
    },
    {
        "name": "get_volume",
        "description": "Obtiene el volumen actual del sistema",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "web_search",
        "description": "Busca información en internet",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "get_datetime",
        "description": "Obtiene la fecha y hora actual del sistema",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "send_telegram",
        "description": "Envía un mensaje de Telegram al usuario",
        "input_schema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
    },
    {
        "name": "write_note",
        "description": "Guarda una nota en ~/Documents/notas-voz.md",
        "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]},
    },
]


# ── Helpers de entorno ────────────────────────────────────────────────────────

def _load_env_var(key: str) -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    for ef in [Path.home() / ".config/gus-monitor.env",
               Path.home() / "Projects/agente-unicaribe/.env"]:
        if ef.exists():
            for line in ef.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip()
    return ""


def _get_api_key() -> str:
    return _load_env_var("ANTHROPIC_API_KEY")


# ── Implementación de herramientas ────────────────────────────────────────────

_BLOCKED = re.compile(
    r'rm\s+-[a-z]*rf?\s*/|'          # rm -rf /
    r'rm\s+--no-preserve-root|'
    r':\(\)\s*\{.*\}|'               # fork bomb
    r'dd\s+if=/dev/zero|'
    r'dd\s+if=/dev/random|'
    r'mkfs\b|'
    r'format\s+[a-z]:|'
    r'>\s*/dev/sd[a-z]\b|'           # write raw to disk
    r'mv\s+.+\s+/dev/null|'
    r'chmod\s+-R\s+[0-7]*7[0-7]*\s+/'  # chmod -R 777 /
)


def run_shell(command: str) -> str:
    if _BLOCKED.search(command):
        return "Bloqueado: ese comando está en la lista de operaciones prohibidas y no se ejecutará nunca."
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=15,
            env={**os.environ, "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-1"},
        )
        out = (r.stdout + r.stderr).strip()
        return out[:500] if out else "(sin salida)"
    except subprocess.TimeoutExpired:
        return "El comando tardó demasiado y fue cancelado."
    except Exception as e:
        return f"Error: {e}"


def open_app(name: str) -> str:
    aliases = {
        "firefox": "firefox", "chrome": "google-chrome-stable",
        "terminal": "alacritty", "alacritty": "alacritty",
        "spotify": "spotify", "archivos": "nautilus",
        "código": "code", "vscode": "code", "obsidian": "obsidian",
    }
    cmd = aliases.get(name.lower(), name)
    try:
        subprocess.Popen(["uwsm-app", "--", cmd],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"{name} abierto."
    except Exception as e:
        return f"No pude abrir {name}: {e}"


def get_clipboard() -> str:
    try:
        r = subprocess.run(["wl-paste"], capture_output=True, text=True)
        return r.stdout.strip()[:300] or "(portapapeles vacío)"
    except Exception as e:
        return f"Error: {e}"


def set_clipboard(text: str) -> str:
    try:
        subprocess.run(["wl-copy"], input=text.encode(), capture_output=True)
        return "Copiado al portapapeles."
    except Exception as e:
        return f"Error: {e}"


def set_volume(percent: int) -> str:
    percent = max(0, min(100, percent))
    try:
        subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent}%"],
                       capture_output=True)
        return f"Volumen al {percent}%."
    except Exception as e:
        return f"Error: {e}"


def get_volume() -> str:
    try:
        r = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                           capture_output=True, text=True)
        return r.stdout.strip()
    except Exception as e:
        return f"Error: {e}"


def web_search(query: str) -> str:
    for cmd in [
        ["firecrawl", "search", query, "--limit", "3"],
        ["ddgr", "--noua", "-n", "3", "--np", query],
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.stdout.strip():
                return r.stdout.strip()[:800]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        except Exception:
            continue
    return "No pude conectarme para buscar."


def get_datetime() -> str:
    return datetime.datetime.now().strftime("%A %d de %B de %Y, %H:%M")


def send_telegram(message: str) -> str:
    token = _load_env_var("TELEGRAM_BOT_TOKEN") or _load_env_var("TELEGRAM_TOKEN")
    chat_id = _load_env_var("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return "No hay configuración de Telegram disponible."
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return "Mensaje enviado por Telegram."
    except Exception as e:
        return f"Error enviando Telegram: {e}"


def write_note(content: str) -> str:
    notes = Path.home() / "Documents/notas-voz.md"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    notes.parent.mkdir(exist_ok=True)
    with open(notes, "a") as f:
        f.write(f"\n## {ts}\n{content}\n")
    return "Nota guardada."


HANDLERS = {
    "run_shell": lambda a: run_shell(a["command"]),
    "open_app": lambda a: open_app(a["name"]),
    "get_clipboard": lambda a: get_clipboard(),
    "set_clipboard": lambda a: set_clipboard(a["text"]),
    "set_volume": lambda a: set_volume(a["percent"]),
    "get_volume": lambda a: get_volume(),
    "web_search": lambda a: web_search(a["query"]),
    "get_datetime": lambda a: get_datetime(),
    "send_telegram": lambda a: send_telegram(a["message"]),
    "write_note": lambda a: write_note(a["content"]),
}


# ── Historial de conversación ─────────────────────────────────────────────────

def load_history(session_file: str) -> list:
    if not os.path.exists(session_file) or not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text()).get("messages", [])[-MAX_MESSAGES:]
    except Exception:
        return []


def _strip_images(messages: list) -> list:
    result = []
    for msg in messages:
        content = msg.get("content", [])
        if isinstance(content, list):
            stripped = [b for b in content if b.get("type") != "image"]
            result.append({**msg, "content": stripped or content})
        else:
            result.append(msg)
    return result


def save_history(messages: list, session_file: str) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps({"messages": _strip_images(messages)}, ensure_ascii=False, indent=2)
    )
    Path(session_file).touch()


def serialize_content(content) -> list:
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return result


# ── Helpers de texto para TTS ────────────────────────────────────────────────

def clean_for_speech(text: str) -> str:
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = text.replace('—', ',').replace('–', ',')
    text = re.sub(r'\n+', ' ', text)
    return re.sub(r'  +', ' ', text).strip()


def emit_sentences(pending: str) -> str:
    parts = re.split(r'(?<=[.!?])\s+', pending)
    for s in parts[:-1]:
        s = clean_for_speech(s).strip()
        if s:
            print(s, flush=True)
    return parts[-1] if parts else ""


# ── Encoding de imágenes ──────────────────────────────────────────────────────

def encode_image(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode()
    ext = path.rsplit(".", 1)[-1].lower()
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/png")
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


# ── Loop del agente ───────────────────────────────────────────────────────────

def run_agent(prompt: str, session_file: str, image_paths: list[str]) -> None:
    client = anthropic.Anthropic(api_key=_get_api_key())
    messages = load_history(session_file)

    user_content: list = []
    for path in image_paths:
        img = encode_image(path)
        if img:
            user_content.append(img)
    user_content.append({"type": "text", "text": prompt})

    messages.append({"role": "user", "content": user_content})

    pending = ""

    while True:
        with client.messages.stream(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            for chunk in stream.text_stream:
                pending += chunk
                pending = emit_sentences(pending)
            response = stream.get_final_message()

        messages.append({"role": "assistant", "content": serialize_content(response.content)})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        tool_results = []
        for tu in tool_uses:
            handler = HANDLERS.get(tu.name)
            result = handler(tu.input) if handler else f"Herramienta '{tu.name}' no disponible."
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})

    if pending.strip():
        s = clean_for_speech(pending).strip()
        if s:
            print(s, flush=True)

    save_history(messages[-MAX_MESSAGES:], session_file)


if __name__ == "__main__":
    session_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/voice-session"
    image_paths = sys.argv[2].split() if len(sys.argv) > 2 and sys.argv[2].strip() else []

    prompt = sys.stdin.read().strip()
    if prompt:
        run_agent(prompt, session_file, image_paths)
