#!/usr/bin/env python3
"""
Servidor TTS usando edge-tts (voz neural) con fallback a piper.
Escucha en un Unix socket, recibe texto, reproduce audio y responde "done".
"""
import asyncio
import os
import signal
import socket
import subprocess
import sys
import tempfile

SOCK = os.path.expanduser("~/.local/share/voz-claude/tts.sock")
VOICE = "es-MX-DaliaNeural"
PIPER = "/usr/bin/piper-tts"
PIPER_MODEL = os.path.expanduser("~/.local/share/piper/es_ES-davefx-medium.onnx")

os.makedirs(os.path.dirname(SOCK), exist_ok=True)
if os.path.exists(SOCK):
    os.unlink(SOCK)


async def speak_edge(text: str) -> bool:
    try:
        import edge_tts
        tmp = tempfile.mktemp(suffix=".mp3")
        communicate = edge_tts.Communicate(text, voice=VOICE)
        with open(tmp, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
        subprocess.run(["mpv", "--really-quiet", "--no-video", tmp],
                       capture_output=True)
        os.unlink(tmp)
        return True
    except Exception:
        return False


def speak_piper(text: str) -> None:
    proc = subprocess.Popen(
        [PIPER, "--model", PIPER_MODEL, "--output_raw"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    raw, _ = proc.communicate(text.encode())
    subprocess.run(
        ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"],
        input=raw, capture_output=True,
    )


def cleanup(*_):
    try:
        os.unlink(SOCK)
    except OSError:
        pass
    sys.exit(0)


signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)


def handle(conn):
    data = b""
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    text = data.decode().strip()
    if text:
        ok = asyncio.run(speak_edge(text))
        if not ok:
            speak_piper(text)
    conn.sendall(b"done\n")
    conn.close()


with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
    srv.bind(SOCK)
    srv.listen(4)
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    while True:
        try:
            conn, _ = srv.accept()
            handle(conn)
        except Exception:
            pass
