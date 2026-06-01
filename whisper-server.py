#!/usr/bin/env python3
import os, socket, signal, sys
from faster_whisper import WhisperModel

SOCK = os.path.expanduser("~/.local/share/voz-claude/whisper.sock")
os.makedirs(os.path.dirname(SOCK), exist_ok=True)

if os.path.exists(SOCK):
    os.unlink(SOCK)

model = WhisperModel("small", device="cpu", compute_type="int8", num_workers=1)

def cleanup(*_):
    try: os.unlink(SOCK)
    except: pass
    sys.exit(0)

signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
    srv.bind(SOCK)
    srv.listen(1)
    sys.stdout.write("ready\n"); sys.stdout.flush()
    while True:
        try:
            conn, _ = srv.accept()
            with conn:
                path = conn.recv(4096).decode().strip()
                if not path or not os.path.exists(path):
                    conn.sendall(b"\n"); continue
                segs, _ = model.transcribe(
                    path, language="es", beam_size=1,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 400},
                    initial_prompt="Español latino. Transcripción directa sin formato.",
                )
                text = " ".join(s.text.strip() for s in segs).strip()
                conn.sendall((text + "\n").encode())
        except Exception:
            try: conn.sendall(b"\n")
            except: pass
