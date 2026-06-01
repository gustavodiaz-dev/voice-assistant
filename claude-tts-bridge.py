#!/usr/bin/env python3
"""
Lee claude --output-format stream-json --verbose desde stdin.
Extrae texto, limpia markdown, emite oraciones en streaming para TTS.
Guarda session_id en el archivo indicado como argv[1].
"""
import sys, json, re


def clean(text):
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'_([^_\n]+)_', r'\1', text)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = text.replace('—', ',').replace('–', ',')
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def emit_sentences(pending):
    """Emite oraciones completas de pending, devuelve el resto."""
    parts = re.split(r'(?<=[.!?])\s+', pending)
    for s in parts[:-1]:
        s = clean(s).strip()
        if s:
            print(s, flush=True)
    return parts[-1] if parts else ""


def main():
    session_file = sys.argv[1] if len(sys.argv) > 1 else None
    pending = ""

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        obj_type = obj.get("type", "")

        if obj_type == "assistant":
            for block in obj.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    chunk = block.get("text", "")
                    if chunk:
                        pending += chunk
                        pending = emit_sentences(pending)

        elif obj_type == "result":
            sid = obj.get("session_id")
            if sid and session_file:
                try:
                    with open(session_file, "w") as f:
                        f.write(sid)
                except OSError:
                    pass

    # Emitir lo que quede al final
    if pending.strip():
        s = clean(pending).strip()
        if s:
            print(s, flush=True)


if __name__ == "__main__":
    main()
