#!/usr/bin/env python3
"""Autenticación única de Google Calendar para la revisión dominical.

Uso (una sola vez, tras colocar el client secret):
    python3 ~/bin/revision-gcal-auth.py

Abre el navegador para dar consentimiento y guarda el token. A partir de
ahí, revision-dominical.py crea los eventos por sí solo (_create_calendar_events).
"""
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Mismas rutas/scopes que revision-dominical.py
GCAL_TOKEN   = Path.home() / ".local/share/voz-claude/google-calendar-token.json"
GCAL_SECRETS = Path.home() / ".config/google-calendar-client.json"
GCAL_SCOPES  = ["https://www.googleapis.com/auth/calendar.events"]


def main() -> None:
    if not GCAL_SECRETS.exists():
        raise SystemExit(
            f"Falta el client secret en {GCAL_SECRETS}.\n"
            "Descárgalo desde Google Cloud Console (OAuth client ID, tipo "
            "'Desktop app') y guárdalo ahí."
        )

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

    # Prueba: leer el calendario primario confirma que todo quedó bien.
    service = build("calendar", "v3", credentials=creds)
    cal = service.calendars().get(calendarId="primary").execute()
    print(f"Listo. Token guardado en {GCAL_TOKEN}")
    print(f"Calendario primario: {cal.get('summary', 'primary')}")


if __name__ == "__main__":
    main()
