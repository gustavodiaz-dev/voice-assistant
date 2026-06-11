#!/usr/bin/env python3
"""
Daemon que monitorea el GuliKit NS39 y lanza el asistente de voz
al presionar el paddle G1 (mapeado a KEY_MENU).
"""

import evdev
import subprocess
import time
import os

CONTROLLER_NAME = "Xbox Wireless Controller"
CONFIG_FILE = os.path.expanduser("~/.local/share/voz-claude/gamepad-button.conf")
DEBOUNCE = 2.0  # segundos mínimos entre activaciones


def find_controller():
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            if CONTROLLER_NAME in dev.name:
                return dev
        except Exception:
            pass
    return None


def learn_button(dev):
    print("[aprendizaje] Presiona el paddle que quieres usar...")
    for event in dev.read_loop():
        if event.type == evdev.ecodes.EV_KEY and event.value == 1:
            code = event.code
            name = evdev.ecodes.BTN.get(code, evdev.ecodes.KEY.get(code, str(code)))
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                f.write(str(code))
            print(f"[aprendizaje] Guardado: {name} (código {code})")
            return code


def load_button():
    try:
        with open(CONFIG_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def run_voice():
    subprocess.Popen(
        ["uwsm-app", "--", os.path.expanduser("~/bin/voz-claude.sh")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def monitor(dev, button_code):
    name = evdev.ecodes.BTN.get(button_code, evdev.ecodes.KEY.get(button_code, str(button_code)))
    print(f"[activo] Escuchando G1 ({name}, código {button_code})")

    last_trigger = 0
    for event in dev.read_loop():
        if event.type != evdev.ecodes.EV_KEY or event.code != button_code or event.value != 1:
            continue
        now = time.monotonic()
        if now - last_trigger >= DEBOUNCE:
            last_trigger = now
            print("[activo] G1 presionado — lanzando asistente de voz")
            run_voice()


def main():
    while True:
        dev = find_controller()
        if not dev:
            time.sleep(10)
            continue

        print(f"[gamepad-voz] Control conectado: {dev.name} en {dev.path}")

        button_code = load_button()
        if button_code is None:
            button_code = learn_button(dev)

        try:
            monitor(dev, button_code)
        except OSError:
            print("[gamepad-voz] Control desconectado.")
            time.sleep(3)


if __name__ == "__main__":
    main()
