#!/usr/bin/env python3
"""
Lee claude --output-format stream-json --verbose desde stdin.
Extrae texto, limpia markdown, emite oraciones para TTS.
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


def split_sentences(text):
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()]


def main():
    session_file = sys.argv[1] if len(sys.argv) > 1 else None

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
                    text = block.get("text", "")
                    if text:
                        for sentence in split_sentences(clean(text)):
                            print(sentence, flush=True)

        elif obj_type == "result":
            sid = obj.get("session_id")
            if sid and session_file:
                try:
                    with open(session_file, "w") as f:
                        f.write(sid)
                except OSError:
                    pass


if __name__ == "__main__":
    main()
