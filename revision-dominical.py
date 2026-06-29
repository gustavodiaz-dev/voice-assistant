#!/usr/bin/env python3
"""
Agente de revisión dominical interactiva por voz.
Claude conduce la entrevista; al final llama submit_revision → n8n → Notion.
stdin   → texto transcrito del usuario (vacío = inicio de sesión)
argv[1] → session file (marca sesión activa)
stdout  → oraciones limpias listas para TTS
"""
import sys
import json
import re
import os
import datetime
import urllib.request
from pathlib import Path

import anthropic
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

MODEL = "claude-sonnet-4-6"
HISTORY_FILE = Path.home() / ".local/share/voz-claude/revision-history.json"
FEEDBACK_FILE = Path.home() / ".local/share/voz-claude/revision-feedback.txt"
MAX_MESSAGES = 200
N8N_WEBHOOK = "https://tailscale.tail7636ea.ts.net/webhook/revision-dominical"
AVANCES_WEBHOOK = "https://tailscale.tail7636ea.ts.net/webhook/avances-actuales"
NOTION_DB_REVISION = "bb1005b1-3f30-40e8-a9a9-fca4d4f15c5b"

GCAL_TOKEN   = Path.home() / ".local/share/voz-claude/google-calendar-token.json"
GCAL_SECRETS = Path.home() / ".config/google-calendar-client.json"
GCAL_SCOPES  = ["https://www.googleapis.com/auth/calendar.events"]
GCAL_TZ      = "America/Puerto_Rico"


# ── Contexto de Notion ────────────────────────────────────────────────────────

def get_last_revision_context(is_monthly: bool = False) -> str:
    token = _load_env_var("NOTION_TOKEN")
    if not token:
        return ""
    try:
        query: dict = {"sorts": [{"timestamp": "created_time", "direction": "descending"}], "page_size": 1}
        if is_monthly:
            query["filter"] = {"property": "Tipo", "select": {"equals": "📊 Mensual profunda"}}
        payload = json.dumps(query).encode()
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{NOTION_DB_REVISION}/query",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Notion-Version": "2022-06-28",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if not data.get("results"):
            return ""
        props = data["results"][0].get("properties", {})

        def txt(key):
            p = props.get(key, {})
            t = p.get("type", "")
            if t == "title":
                items = p.get("title", [])
            elif t == "rich_text":
                items = p.get("rich_text", [])
            elif t == "select":
                sel = p.get("select")
                return sel["name"] if sel else ""
            else:
                return ""
            return items[0]["plain_text"] if items else ""

        semana        = next((txt(k) for k, v in props.items() if v.get("type") == "title"), "")
        tres_acciones = txt("3 acciones top")
        foco          = txt("Foco próxima semana")
        energia       = txt("Energía")
        lines = []

        if is_monthly:
            hitos     = txt("Hitos del mes")
            ajustes   = txt("Ajustes al sistema")
            reflexion = txt("Reflexión profunda")
            logro     = txt("Logro del mes")
            foco_mes  = txt("Foco del próximo mes")
            if semana:
                lines.append(f"Último mes revisado: {semana} (energía: {energia})")
            if logro:
                lines.append(f"Logro principal que declaraste: {logro}")
            if tres_acciones:
                lines.append(f"Los 3 compromisos que hiciste para ese mes: {tres_acciones}")
            if foco_mes:
                lines.append(f"Foco del mes: {foco_mes}")
            if hitos:
                lines.append(f"Hitos del mes anterior: {hitos}")
            if ajustes:
                lines.append(f"Ajustes que te comprometiste a hacer: {ajustes}")
            if reflexion:
                lines.append(f"Reflexión del mes anterior: {reflexion}")
        else:
            ingles    = txt("Inglés — avance")
            trabajo   = txt("Trabajo nuevo — avance")
            usa2028   = txt("USA 2028 — avance")
            homelab   = txt("Homelab — avance")
            finanzas  = txt("Finanzas — avance")
            univ      = txt("Universidad — avance")
            if semana:
                lines.append(f"Última revisión: {semana} (energía: {energia})")
            if foco:
                lines.append(f"Foco declarado la semana pasada: {foco}")
            if tres_acciones:
                lines.append(f"Las 3 acciones que te comprometiste: {tres_acciones}")
            lines.append("\nLo que reportaste por área la semana pasada:")
            for etiqueta, valor in [
                ("Inglés", ingles), ("Trabajo nuevo", trabajo),
                ("USA 2028", usa2028), ("Homelab", homelab),
                ("Finanzas", finanzas), ("Universidad", univ),
            ]:
                lines.append(f"  {etiqueta}: {valor or 'sin registro'}")
        return "\n".join(lines)
    except Exception:
        return ""


# ── Detección de modo ─────────────────────────────────────────────────────────

def is_last_sunday() -> bool:
    today = datetime.date.today()
    if today.weekday() != 6:
        return False
    return (today + datetime.timedelta(days=7)).month != today.month


def week_label() -> str:
    today = datetime.date.today()
    days_since_last_sunday = (today.weekday() + 1) % 7
    end = today - datetime.timedelta(days=days_since_last_sunday)
    start = end - datetime.timedelta(days=6)
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    if start.month == end.month:
        return f"Semana del {start.day} al {end.day} de {meses[end.month - 1]} {end.year}"
    return (f"Semana del {start.day} de {meses[start.month - 1]}"
            f" al {end.day} de {meses[end.month - 1]} {end.year}")


def month_label() -> str:
    today = datetime.date.today()
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{meses[today.month - 1]} {today.year}"


# ── System prompt ─────────────────────────────────────────────────────────────

def load_feedback() -> str:
    try:
        return FEEDBACK_FILE.read_text().strip()
    except Exception:
        return ""


def _base_rules(modo: str, semana: str, es_mensual: bool) -> str:
    duracion = "35-45" if es_mensual else "20-25"
    periodo = "mes" if es_mensual else "semana"
    return f"""REGLAS DE CONVERSACIÓN:
- Saluda brevemente al abrir. Explica el modo ({modo}, aprox. {duracion} min).
- Haz una pregunta a la vez. Espera la respuesta antes de pasar al siguiente tema.
- Oraciones cortas y naturales para TTS. Sin guiones largos. Sin listas con viñetas.
- Si el usuario dice "sigue", "pasa" o "nada más", avanza sin insistir.
- No inventes ni asumas datos; solo registra lo que el usuario mencione.
- Si el usuario te pide revisar Notion o el sistema, o dice que no sabes algo que sí podrías consultar, llama `consultar_avances_actuales` antes de responder — sí tienes acceso en vivo, úsalo en vez de decir que no puedes consultarlo.
- CRÍTICO: Llama `submit_revision` SOLO cuando hayas cubierto todas las secciones de esta revisión. Nunca llames submit_revision con datos incompletos.
- El campo semana de submit_revision es SIEMPRE exactamente: {semana}
- Antes de cerrar, haz UNA SOLA pregunta: "¿Hay algo que debería preguntarte diferente la próxima {periodo}?" Escucha la respuesta.
- Llama `save_feedback` con esa respuesta. Luego llama `submit_revision` con todos los datos.
- Tras confirmación de guardado, despídete brevemente."""


def build_system_prompt(modo: str, semana: str, fecha: str, last_revision: str = "") -> str:
    es_mensual = "Mensual" in modo
    feedback = load_feedback()
    feedback_section = (
        f"\n\nFEEDBACK ANTERIOR (aplícalo):\n{feedback}\n"
    ) if feedback else ""
    revision_label = "MES ANTERIOR" if es_mensual else "SEMANA PASADA"
    revision_section = (
        f"\n\nCONTEXTO DE NOTION — {revision_label}:\n{last_revision}\n"
        f"{'IMPORTANTE: usa el avance por área como punto de partida de cada pregunta. No preguntes qué pasó; dile lo que ya sabes y pide el update.' if not es_mensual else ''}"
    ) if last_revision else ""

    base = f"Eres el conductor de la revisión {'mensual profunda' if es_mensual else 'semanal'} de Gustavo. Entrevístale de forma conversacional, natural y cálida.{feedback_section}{revision_section}\nMODO: {modo}\nFECHA: {fecha}\n"

    if es_mensual:
        return base + f"""
ESTRUCTURA DE LA REVISIÓN MENSUAL (sigue este orden estricto):

1. APERTURA
   Pide una temperatura del mes del 1 al 10 y cuál fue su logro más importante.
   Si tienes contexto del mes anterior, menciona los compromisos que hizo y pregunta cómo le fue con ellos.

2. ÁREAS (una por una, preguntas específicas del mes completo):

   🗣️ INGLÉS: horas promedio por semana, total de páginas del libro, vocabulario que se consolidó, output producido (escribir, hablar, podcasts).

   💼 TRABAJO NUEVO: total de aplicaciones enviadas en el mes, respuestas recibidas, entrevistas realizadas, qué aprendió del proceso de búsqueda.

   🇺🇸 USA 2028: avance en certifications (¿qué estudió?, ¿algún examen programado?), actividad en GitHub, avances concretos en trámites de migración (I-130 u otros).

   🏠 HOMELAB & TECNOLOGÍA: proyectos terminados este mes, proyectos que quedaron pendientes, estado del Agente Personal (bot Telegram) y del Asistente de Voz, workflows nuevos en n8n.

   💰 FINANZAS: balance general del mes, ahorro logrado vs meta, gastos inesperados, ¿cumplió el plan económico?

   🎓 UNIVERSIDAD: calificaciones actuales por materia, entregas del mes, ¿alguna materia en riesgo?

3. REVISIÓN DEL SISTEMA
   Dos preguntas: ¿Qué hábitos o rutinas mantuviste este mes? ¿El horario semanal fue realista o lo ignoraste?

4. REFLEXIÓN PROFUNDA (estas 3 preguntas, en este orden exacto):
   a. ¿Qué fue lo más importante que aprendiste este mes?
   b. ¿Qué postergaste que realmente importa?
   c. ¿Cómo se alinea lo que lograste este mes con donde quieres estar en USA 2028?

5. CIERRE — MES SIGUIENTE
   Pregunta: ¿cuál es el foco del próximo mes en una frase?
   Luego: ¿cuáles son sus 3 compromisos concretos para ese mes?
   Finalmente: propone bloques de horario para la próxima semana. Constraints fijos: lunes 6-10pm ocupado, sábado intocable. Confirma con el usuario.

CAMPOS OBLIGATORIOS en submit_revision (mensuales):
   temperatura_mes, logro_mes, ingles_avance, trabajo_nuevo_avance, usa2028_avance,
   homelab_avance, finanzas_avance, universidad_avance, revision_sistema,
   reflexion_profunda, mes_siguiente_foco, decision_postergada, tres_acciones_top,
   hitos_del_mes, energia, horario_proxima_semana

{_base_rules(modo, semana, es_mensual)}"""

    else:
        return base + f"""
SEMANA: {semana}

ÁREAS A CUBRIR (en este orden):
1. 🗣️ Inglés — sesiones, lectura, output, vocabulario, libro (páginas + palabras nuevas)
2. 💼 Trabajo nuevo — aplicaciones enviadas, entrevistas, materiales actualizados (CV, LinkedIn)
3. 🇺🇸 USA 2028 — carrera tech (DevOps, certs, GitHub) + migración (I-130, trámites)
4. 🏠 Homelab & Tecnología — Proxmox, Agente Personal, Asistente de Voz, n8n
5. 💰 Finanzas — plan económico, ahorro, gastos inesperados
6. 🎓 Universidad (Unicaribe) — materias activas, entregas, calificaciones

MODO DE ENTREVISTA:
Si tienes contexto de Notion, úsalo así:
- Al abrir: menciona el foco y las 3 acciones comprometidas. Pregunta en general cómo fue la semana.
- En cada área: presenta lo que ya sabes ("la semana pasada reportaste X") y pregunta qué cambió, qué avanzó o qué quedó igual. No preguntes desde cero como si no supieras nada.
- Si no hay contexto de un área, entonces sí pregunta normalmente.

CIERRE (obligatorio):
- Energía general de la semana: Alta, Media, Baja o Agotado.
- Decisiones importantes tomadas.
- Las 3 acciones top para la próxima semana.
- El foco de la próxima semana (una frase).
- Proyección de horarios: propone un bloque concreto por proyecto. Constraints: lunes 6-10pm ocupado, sábado intocable. Confirma antes de cerrar.

Si no hubo avance en un área, registra "Sin avance esta semana" y continúa.

{_base_rules(modo, semana, es_mensual)}"""


# ── Google Calendar ───────────────────────────────────────────────────────────

def _get_gcal_service():
    creds = None
    if GCAL_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(GCAL_TOKEN), GCAL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(GCAL_SECRETS), GCAL_SCOPES)
            creds = flow.run_local_server(port=0)
        GCAL_TOKEN.parent.mkdir(parents=True, exist_ok=True)
        GCAL_TOKEN.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def _create_calendar_events(bloques: list) -> str:
    if not bloques:
        return ""
    if not GCAL_SECRETS.exists():
        return "Sin credenciales de Google Calendar (falta ~/.config/google-calendar-client.json)."
    try:
        service = _get_gcal_service()
        today = datetime.date.today()
        days_to_monday = (7 - today.weekday()) % 7 or 7
        next_monday = today + datetime.timedelta(days=days_to_monday)
        dias_map = {
            "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
            "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
        }
        created = []
        for bloque in bloques:
            dia = bloque.get("dia", "").lower()
            offset = dias_map.get(dia)
            if offset is None:
                continue
            event_date = next_monday + datetime.timedelta(days=offset)
            inicio  = bloque.get("inicio", "18:00")
            fin     = bloque.get("fin",    "20:00")
            proyecto = bloque.get("proyecto", "Proyecto")
            event = {
                "summary": proyecto,
                "start": {"dateTime": f"{event_date}T{inicio}:00", "timeZone": GCAL_TZ},
                "end":   {"dateTime": f"{event_date}T{fin}:00",    "timeZone": GCAL_TZ},
                "description": "Bloque de proyecto — revisión dominical",
            }
            service.events().insert(calendarId="primary", body=event).execute()
            created.append(f"{proyecto} el {dia} {inicio}–{fin}")
        if created:
            return f"Eventos creados en Google Calendar: {', '.join(created)}."
        return ""
    except Exception as e:
        return f"No pude crear los eventos en Google Calendar: {e}"


# ── Tool: submit_revision ─────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "save_feedback",
        "description": (
            "Guarda el feedback del usuario sobre cómo mejorar la próxima revisión. "
            "Llámala justo antes de submit_revision, con lo que el usuario respondió a la pregunta de mejora."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "feedback": {
                    "type": "string",
                    "description": "Lo que el usuario dijo que se puede mejorar, o 'Sin cambios' si está conforme."
                }
            },
            "required": ["feedback"],
        },
    },
    {
        "name": "consultar_avances_actuales",
        "description": (
            "Consulta en vivo el estado real de los avances de Gustavo (Notion: backlog, materias, proyectos, "
            "topes; más fitness y hábito de inglés de los últimos 7 días). Úsala si el usuario te pide revisar "
            "Notion o el sistema antes de responder, o si necesitas contexto fresco de un área que el contexto "
            "de la semana pasada no cubre."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_revision",
        "description": (
            "Guarda la revisión completada en Notion vía n8n. "
            "SOLO llamar cuando se hayan cubierto las 6 áreas Y todos los ítems del cierre. "
            "Nunca llamar con datos incompletos o en medio de la entrevista."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "semana":                 {"type": "string", "description": "Título exacto de la semana indicado en el system prompt"},
                "tipo":                   {"type": "string", "enum": ["🗓️ Semanal", "📊 Mensual profunda"]},
                "energia":                {"type": "string", "enum": ["🔋 Alta", "⚡ Media", "🪫 Baja", "🛌 Agotado"]},
                "ingles_avance":          {"type": "string"},
                "libro_paginas":          {"type": "number", "description": "Páginas leídas del libro en inglés esta semana"},
                "libro_vocabulario":      {"type": "string", "description": "Palabras o expresiones nuevas aprendidas"},
                "trabajo_nuevo_avance":   {"type": "string", "description": "Búsqueda activa de empleo: aplicaciones, entrevistas, materiales"},
                "usa2028_avance":         {"type": "string", "description": "Carrera tech (DevOps, certs, GitHub) + migración (I-130, trámites)"},
                "homelab_avance":         {"type": "string", "description": "Proxmox, Agente Personal, Asistente de Voz, n8n, otros proyectos técnicos"},
                "finanzas_avance":        {"type": "string"},
                "universidad_avance":     {"type": "string"},
                "decisiones_tomadas":     {"type": "string"},
                "tres_acciones_top":      {"type": "string"},
                "foco_proxima_semana":    {"type": "string"},
                "hitos_del_mes":          {"type": "string", "description": "Solo revisión mensual: logros más importantes del mes"},
                "ajustes_al_sistema":     {"type": "string", "description": "Solo revisión mensual: cambios a rutinas, presupuesto o herramientas"},
                "reflexion_profunda":     {"type": "string", "description": "Solo revisión mensual: las 3 reflexiones profundas consolidadas"},
                "temperatura_mes":        {"type": "number", "description": "Solo revisión mensual: temperatura del mes del 1 al 10"},
                "logro_mes":              {"type": "string", "description": "Solo revisión mensual: el logro más importante del mes"},
                "revision_sistema":       {"type": "string", "description": "Solo revisión mensual: hábitos y rutinas — qué funcionó, qué falló"},
                "mes_siguiente_foco":     {"type": "string", "description": "Solo revisión mensual: el foco principal del próximo mes en una frase"},
                "decision_postergada":    {"type": "string", "description": "Solo revisión mensual: lo más importante que postergó este mes"},
                "horario_proxima_semana": {
                    "type": "array",
                    "description": "Proyección de bloques por proyecto. Un objeto por cada bloque proyecto+día.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "proyecto": {"type": "string", "description": "Nombre del proyecto. Ej: Inglés, Homelab, Finanzas, Universidad"},
                            "dia":      {"type": "string", "description": "Día de la semana en español minúsculas. Ej: lunes, martes, miércoles"},
                            "inicio":   {"type": "string", "description": "Hora inicio HH:MM. Ej: 18:00"},
                            "fin":      {"type": "string", "description": "Hora fin HH:MM. Ej: 20:00"}
                        },
                        "required": ["proyecto", "dia", "inicio", "fin"]
                    }
                },
            },
            "required": [
                "semana", "tipo", "energia",
                "ingles_avance", "trabajo_nuevo_avance", "usa2028_avance",
                "homelab_avance", "finanzas_avance", "universidad_avance",
                "tres_acciones_top", "foco_proxima_semana",
            ],
        },
    }
]


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _save_feedback(feedback: str) -> str:
    try:
        FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.date.today().isoformat()
        FEEDBACK_FILE.write_text(f"[{ts}] {feedback}")
        return "Feedback guardado. Lo tendré en cuenta la próxima semana."
    except Exception as e:
        return f"No pude guardar el feedback: {e}"


def _submit_revision(data: dict) -> str:
    try:
        payload = json.dumps(data, ensure_ascii=False).encode()
        req = urllib.request.Request(
            N8N_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result.get("message", "Revisión guardada en Notion correctamente.")
    except Exception as e:
        return f"No pude conectar con n8n: {e}. Los datos quedan en el historial."


def _consultar_avances_actuales() -> str:
    try:
        req = urllib.request.Request(AVANCES_WEBHOOK, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            return json.dumps(data.get("avances", data), ensure_ascii=False)
    except Exception as e:
        return f"No pude consultar los avances en vivo: {e}"


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


# ── Historial ─────────────────────────────────────────────────────────────────

def _has_tool_result(msg: dict) -> bool:
    c = msg.get("content")
    return isinstance(c, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in c
    )


def _has_tool_use(msg: dict) -> bool:
    c = msg.get("content")
    return isinstance(c, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_use" for b in c
    )


def safe_window(messages: list) -> list:
    """Devuelve los últimos MAX_MESSAGES sin romper pares tool_use/tool_result.

    La API de Anthropic rechaza una conversación que empieza con un tool_result
    huérfano (su tool_use quedó fuera de la ventana) o que termina con un
    tool_use sin su resultado. Recortamos ambos extremos para que el historial
    siempre sea reanudable.
    """
    window = messages[-MAX_MESSAGES:]
    while window and (window[0].get("role") != "user" or _has_tool_result(window[0])):
        window.pop(0)
    while window and window[-1].get("role") == "assistant" and _has_tool_use(window[-1]):
        window.pop()
    return window


def load_history(session_file: str) -> list:
    if not os.path.exists(session_file) or not HISTORY_FILE.exists():
        return []
    try:
        return safe_window(json.loads(HISTORY_FILE.read_text()).get("messages", []))
    except Exception:
        return []


def save_history(messages: list, session_file: str) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps({"messages": messages}, ensure_ascii=False, indent=2)
    )
    Path(session_file).touch()


def clear_session(session_file: str) -> None:
    HISTORY_FILE.unlink(missing_ok=True)
    try:
        Path(session_file).unlink(missing_ok=True)
    except Exception:
        pass


def serialize_content(content) -> list:
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({"type": "tool_use", "id": block.id,
                           "name": block.name, "input": block.input})
    return result


# ── Loop principal ────────────────────────────────────────────────────────────

def run_agent(prompt: str, session_file: str) -> None:
    es_mensual = is_last_sunday()
    modo = "📊 Mensual profunda" if es_mensual else "🗓️ Semanal"
    semana = month_label() if es_mensual else week_label()
    fecha = datetime.date.today().isoformat()
    last_revision = get_last_revision_context(is_monthly=es_mensual) if not os.path.exists(session_file) else ""
    system = build_system_prompt(modo, semana, fecha, last_revision)

    client = anthropic.Anthropic(api_key=_load_env_var("ANTHROPIC_API_KEY"))
    messages = load_history(session_file)

    # Primera vez: Claude arranca con el saludo
    if not messages:
        messages.append({"role": "user", "content": "Iniciemos."})
    elif prompt.strip():
        messages.append({"role": "user", "content": prompt.strip()})

    pending = ""
    review_done = False

    while True:
        with client.messages.stream(
            model=MODEL,
            max_tokens=1024,
            system=system,
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
            if tu.name == "save_feedback":
                result = _save_feedback(tu.input.get("feedback", ""))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                })
            elif tu.name == "consultar_avances_actuales":
                result = _consultar_avances_actuales()
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                })
            elif tu.name == "submit_revision":
                result = _submit_revision(tu.input)
                bloques = tu.input.get("horario_proxima_semana") or []
                if bloques:
                    cal_result = _create_calendar_events(bloques)
                    if cal_result:
                        result = f"{result} {cal_result}"
                review_done = True
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                })
            else:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": "Herramienta no disponible.",
                })

        messages.append({"role": "user", "content": tool_results})

        if review_done:
            # Un turno más para que Claude confirme el guardado y se despida
            with client.messages.stream(
                model=MODEL,
                max_tokens=256,
                system=system,
                tools=TOOLS,
                messages=messages,
            ) as stream:
                for chunk in stream.text_stream:
                    pending += chunk
                    pending = emit_sentences(pending)
                final = stream.get_final_message()
            messages.append({"role": "assistant",
                             "content": serialize_content(final.content)})
            clear_session(session_file)
            break

    if pending.strip():
        s = clean_for_speech(pending).strip()
        if s:
            print(s, flush=True)

    if not review_done:
        save_history(safe_window(messages), session_file)


if __name__ == "__main__":
    session_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/revision-session"
    prompt = sys.stdin.read().strip()
    run_agent(prompt, session_file)
