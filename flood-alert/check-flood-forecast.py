import os
import sys
import smtplib
import ssl
import requests
from email.message import EmailMessage
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import time
import random


FORECAST_URL = (
    "https://www.noe.gv.at/wasserstand/kidata/stationdata/208009_DurchflussPrognose_12Stunden.csv"
)

# Schwellenwerte (m^3/s)
THRESHOLDS: Dict[str, float] = {
    "HQ1": 36.0,
    "HQ2": 75.0,
    "HQ5": 110.0,
    "HQ10": 150.0,
    "HQ30": 200.0,
    "HQ100": 240.0,
}


def fetch_csv_text(url: str) -> str:
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    # Erst UTF-8, dann Fallback, falls Umlaute falsch erscheinen
    try:
        text = response.content.decode("utf-8")
        if "Ã" in text or "Â" in text:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "mojibake detected")
        return text
    except Exception:
        return response.content.decode("latin-1", errors="replace")


def fetch_csv_text_with_retry(url: str, max_attempts: int = 10, base_delay_seconds: float = 2.0) -> str:
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_csv_text(url)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            # Exponentielles Backoff mit kleinem Jitter
            delay = min(60.0, base_delay_seconds * (2 ** (attempt - 1)))
            delay += random.uniform(0, 0.5)
            time.sleep(delay)
        except Exception as exc:
            # Nicht-Netzwerkfehler nicht erneut versuchen
            raise
    assert last_exc is not None
    raise last_exc


def parse_forecast(csv_text: str) -> List[Tuple[datetime, float]]:
    lines = [line.strip() for line in csv_text.splitlines() if line.strip()]
    # Suche nach dem Header "Datum;Mittel"
    try:
        start_idx = next(
            i for i, line in enumerate(lines) if line.lower().startswith("datum;mittel")
        )
    except StopIteration:
        raise ValueError("CSV-Format unerwartet: 'Datum;Mittel' nicht gefunden")

    data: List[Tuple[datetime, float]] = []
    for line in lines[start_idx + 1 :]:
        # Erwartet Format: "YYYY-MM-DD HH:MM:SS;value"
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 2:
            continue
        try:
            ts = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
            value = float(parts[1].replace(",", "."))
        except Exception:
            # Sobald ein anderer Block kommt, abbrechen
            continue
        data.append((ts, value))

    if not data:
        raise ValueError("Keine Prognosedaten gefunden")
    return data


def find_threshold_crossings(
    forecast: List[Tuple[datetime, float]], thresholds: Dict[str, float]
) -> Dict[str, Optional[Tuple[datetime, float]]]:
    # Für jede Schwelle die erste Zeit finden, an der sie erreicht oder überschritten wird
    result: Dict[str, Optional[Tuple[datetime, float]]] = {}
    # sortiere nach Wert aufsteigend, um kleinste Schwellen zuerst zu prüfen
    for name, limit in sorted(thresholds.items(), key=lambda kv: kv[1]):
        crossing: Optional[Tuple[datetime, float]] = None
        for ts, value in forecast:
            if value >= limit:
                crossing = (ts, value)
                break
        result[name] = crossing
    return result


def build_issue_body(
    crossings: Dict[str, Optional[Tuple[datetime, float]]],
    sample_span: Tuple[datetime, datetime],
) -> str:
    start_ts, end_ts = sample_span
    lines: List[str] = []
    lines.append("Automatische Hochwasserwarnung – Prognose Atzenbrugg (Bundesstraßenbrücke)")
    lines.append("")
    lines.append(
        f"Zeitraum der Prognose: {start_ts.strftime('%Y-%m-%d %H:%M')} bis {end_ts.strftime('%Y-%m-%d %H:%M')} (lokale Zeit)"
    )
    lines.append("")
    lines.append("Schwellenüberschreitungen (m³/s):")
    for name, data in sorted(crossings.items(), key=lambda kv: THRESHOLDS[kv[0]]):
        if data is None:
            lines.append(f"- {name} ({THRESHOLDS[name]:.0f}): nicht prognostiziert")
        else:
            ts, value = data
            lines.append(
                f"- {name} ({THRESHOLDS[name]:.0f}): {ts.strftime('%Y-%m-%d %H:%M')} ≈ {value:.2f}"
            )
    lines.append("")
    lines.append(f"Quelle: {FORECAST_URL}")
    return "\n".join(lines)


def send_email(
    smtp_server: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    sender_email: str,
    recipients: List[str],
    subject: str,
    body: str,
) -> None:
    if not recipients:
        raise ValueError("Keine Empfänger für E-Mail definiert")

    message = EmailMessage()
    message["From"] = sender_email
    message["To"] = ", ".join([r.strip() for r in recipients if r.strip()])
    message["Subject"] = subject
    message.set_content(body)

    # TLS/SSL je nach Port
    if smtp_port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context) as server:
            if smtp_username and smtp_password:
                server.login(smtp_username, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            try:
                server.starttls(context=ssl.create_default_context())
            except smtplib.SMTPException:
                pass
            if smtp_username and smtp_password:
                server.login(smtp_username, smtp_password)
            server.send_message(message)


def main() -> int:
    csv_text = fetch_csv_text_with_retry(FORECAST_URL)
    forecast = parse_forecast(csv_text)

    # Zeitraum ermitteln (erste und letzte Zeit in Prognose)
    start_ts, _ = forecast[0]
    end_ts, _ = forecast[-1]

    crossings = find_threshold_crossings(forecast, THRESHOLDS)

    # Nur melden, wenn mindestens HQ1 erreicht wird
    hq1 = crossings.get("HQ1")
    if hq1 is None:
        print("Keine HQ1-Überschreitung in Prognose. Keine Aktion.")
        return 0

    body = build_issue_body(crossings, (start_ts, end_ts))
    title = "Hochwasserwarnung – Durchflussprognose HQ1+ erreicht (Atzenbrugg)"

    # SMTP-Parameter aus Umgebungsvariablen
    smtp_server = os.getenv("SMTP_SERVER", "").strip()
    smtp_port_str = os.getenv("SMTP_PORT", "").strip()
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    sender_email = os.getenv("SMTP_SENDER_EMAIL", "").strip()
    # Priorität: SMTP_NOTIFY_MAIL > SMTP_SENDER_EMAIL
    recipients_raw = os.getenv("SMTP_NOTIFY_MAIL", "").strip()
    if not recipients_raw:
        recipients_raw = sender_email
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

    try:
        smtp_port = int(smtp_port_str)
    except ValueError:
        print("SMTP_PORT ist ungültig oder fehlt", file=sys.stderr)
        return 2

    missing = [
        name
        for name, val in [
            ("SMTP_SERVER", smtp_server),
            ("SMTP_USERNAME", smtp_username),
            ("SMTP_PASSWORD", smtp_password),
            ("SMTP_SENDER_EMAIL", sender_email),
        ]
        if not val
    ]
    if missing:
        print(f"Fehlende SMTP-Umgebungsvariablen: {', '.join(missing)}", file=sys.stderr)
        return 2

    send_email(
        smtp_server=smtp_server,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        sender_email=sender_email,
        recipients=recipients,
        subject=title,
        body=body,
    )
    print("Warn-E-Mail gesendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


