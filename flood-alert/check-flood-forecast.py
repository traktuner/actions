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


def _split_semicolon(line: str) -> List[str]:
    # Semikolon trennen; Felder nicht über Prozess-CSV-Parsing interpretieren, damit
    # Werte wie "0,02" oder "m³/s" unverändert bleiben.
    return [p.strip() for p in line.split(";")]


def _find_data_columns(header_fields: List[str]) -> Tuple[int, int]:
    """Erkennt Datum- und Wertspalte robust über den Header.

    Akzeptiert verschiedene Bezeichner (Datum/Zeit/Datum Zeit ... für die
    Zeit-Spalte, Mittel/Wert/Q/Durchfluss ... für die Wert-Spalte), damit
    kleinere Umbenennungen durch die Datenquelle nicht zum Ausfall führen.
    """
    date_idx = -1
    value_idx = -1

    date_keywords = ("datum", "zeit", "date", "time")
    value_keywords = ("mittel", "wert", "value", "durchfluss", "q", "abfluss", "prognose")

    normalized = [f.lower() for f in header_fields]
    for i, field in enumerate(normalized):
        if date_idx < 0 and any(kw in field for kw in date_keywords):
            date_idx = i
            continue
        if value_idx < 0 and any(kw in field for kw in value_keywords):
            value_idx = i

    # Fallback: erste Spalte Datum, zweite Spalte Wert (klassisches Format)
    if date_idx < 0 and len(header_fields) >= 1:
        date_idx = 0
    if value_idx < 0 and len(header_fields) >= 2:
        # Zweite Spalte, sofern sie nicht schon die Datumsspalte ist
        value_idx = 1 if date_idx != 1 else (2 if len(header_fields) >= 3 else -1)

    if date_idx < 0 or value_idx < 0 or date_idx == value_idx:
        raise ValueError(
            f"CSV-Header-Spalten konnten nicht erkannt werden: {header_fields}"
        )
    return date_idx, value_idx


def parse_forecast(csv_text: str) -> List[Tuple[datetime, float]]:
    lines = [line.strip() for line in csv_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Leere Antwort der Datenquelle (keine Zeilen)")

    # Header-Zeile erkennen: erste Zeile, die (case-insensitiv) eines der typischen
    # Schlüsselwörter für Datum/Zeit enthält. Früher war das exakt "Datum;Mittel".
    start_idx = -1
    for i, line in enumerate(lines):
        lowered = line.lower()
        if any(kw in lowered for kw in ("datum", "date", "zeit")) and ";" in line:
            start_idx = i
            break

    if start_idx < 0:
        # Kompatibilität: altes exaktes Matching als letzter Versuch
        for i, line in enumerate(lines):
            if line.lower().startswith("datum;mittel"):
                start_idx = i
                break

    if start_idx < 0:
        raise ValueError(
            "CSV-Format unerwartet: keine Header-Zeile mit Datum/Zeit-Spalte gefunden"
        )

    header_fields = _split_semicolon(lines[start_idx])
    try:
        date_idx, value_idx = _find_data_columns(header_fields)
    except ValueError as exc:
        raise ValueError(f"CSV-Header nicht parsebar: {exc} (Header: {header_fields})")

    # Liste aller unterstützten Datum-Zeit-Formate
    datetime_formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    )

    data: List[Tuple[datetime, float]] = []
    parse_errors: List[str] = []
    empty_values = 0

    for line in lines[start_idx + 1 :]:
        parts = _split_semicolon(line)
        # Zeile muss mindestens bis zur Wertspalte reichen
        if len(parts) <= max(date_idx, value_idx):
            continue

        value_str = parts[value_idx].strip()
        if not value_str:
            empty_values += 1
            continue

        date_str = parts[date_idx].strip()
        ts: Optional[datetime] = None
        for fmt in datetime_formats:
            try:
                ts = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue
        if ts is None:
            parse_errors.append(f"Datumsformat nicht erkannt: {date_str!r}")
            continue

        try:
            value = float(value_str.replace(",", "."))
        except ValueError:
            parse_errors.append(f"Wert nicht parsebar: {value_str!r}")
            continue

        data.append((ts, value))

    if not data:
        # Differenzierte Fehlermeldung hilft bei der Diagnose von Formatänderungen
        detail_parts: List[str] = []
        if empty_values:
            detail_parts.append(f"{empty_values} Zeile(n) mit leerem Wert")
        if parse_errors:
            detail_parts.append(f"{len(parse_errors)} Parse-Fehler: {parse_errors[:3]}")
        if not detail_parts:
            detail_parts.append("keine Datenzeilen nach Header gefunden")
        raise ValueError(
            "Keine Prognosedaten gefunden (alle Werte sind leer oder ungültig) – "
            + "; ".join(detail_parts)
        )
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


def load_smtp_config() -> Tuple[str, int, str, str, str, List[str]]:
    """Lädt und validiert SMTP-Konfiguration aus Umgebungsvariablen."""
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
        raise ValueError("SMTP_PORT ist ungültig oder fehlt")

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
        raise ValueError(f"Fehlende SMTP-Umgebungsvariablen: {', '.join(missing)}")

    return smtp_server, smtp_port, smtp_username, smtp_password, sender_email, recipients


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


def _build_diagnostic_sample(csv_text: str, max_lines: int = 25) -> str:
    """Erzeugt einen kompakten Diagnose-Snapshot der Rohdaten für Fehlermails."""
    raw_lines = csv_text.splitlines()
    total = len(raw_lines)
    shown = raw_lines[:max_lines]
    suffix = ""
    if total > max_lines:
        suffix = f"\n... ({total - max_lines} weitere Zeilen abgeschnitten)"
    return f"(gesamt {total} Zeilen)\n" + "\n".join(shown) + suffix


def _write_github_summary(forecast: List[Tuple[datetime, float]], crossings: Dict[str, Optional[Tuple[datetime, float]]]) -> None:
    """Schreibt eine Markdown-Zusammenfassung der Prognose nach $GITHUB_STEP_SUMMARY."""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return  # Lokal oder ohne Summary-Umgebung → nichts tun

    lines: List[str] = []
    lines.append("## Hochwasser-Prognose Atzenbrugg (Bundesstraßenbrücke)")
    lines.append("")
    lines.append(f"**Zeitraum**: {forecast[0][0].strftime('%Y-%m-%d %H:%M')} bis {forecast[-1][0].strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("| Zeit | Durchfluss (m³/s) |")
    lines.append("|------|-------------------|")
    for ts, value in forecast:
        lines.append(f"| {ts.strftime('%Y-%m-%d %H:%M')} | {value:.2f} |")
    lines.append("")
    lines.append("### Schwellenüberschreitungen")
    lines.append("")
    for name, data in sorted(crossings.items(), key=lambda kv: THRESHOLDS[kv[0]]):
        if data:
            ts, value = data
            lines.append(f"- **{name}** ({THRESHOLDS[name]:.0f} m³/s): erreicht um {ts.strftime('%Y-%m-%d %H:%M')} ≈ {value:.2f} m³/s")
        else:
            lines.append(f"- **{name}** ({THRESHOLDS[name]:.0f} m³/s): nicht prognostiziert")
    lines.append("")
    lines.append(f"*Quelle: {FORECAST_URL}*")

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> int:
    try:
        csv_text = fetch_csv_text_with_retry(FORECAST_URL)
    except Exception as exc:
        # Datenquelle antwortet gar nicht oder nur mit Fehler – sofort informieren
        print(f"Fehler beim Abruf der Datenquelle: {exc}", file=sys.stderr)
        try:
            smtp_server, smtp_port, smtp_username, smtp_password, sender_email, recipients = load_smtp_config()
            body = (
                "Automatische Hochwasserwarnung – Datenquelle nicht erreichbar\n\n"
                f"Die Datenquelle konnte nach mehrfachen Versuchen nicht abgerufen werden.\n\n"
                f"Fehlermeldung: {exc}\n\n"
                f"Quelle: {FORECAST_URL}\n"
            )
            send_email(
                smtp_server=smtp_server,
                smtp_port=smtp_port,
                smtp_username=smtp_username,
                smtp_password=smtp_password,
                sender_email=sender_email,
                recipients=recipients,
                subject="Hochwasserwarnung – Datenquelle nicht erreichbar (Atzenbrugg)",
                body=body,
            )
            print("Benachrichtigungs-E-Mail gesendet.")
        except ValueError as smtp_err:
            print(f"Fehler beim Laden der SMTP-Konfiguration: {smtp_err}", file=sys.stderr)
            return 2
        return 0

    try:
        forecast = parse_forecast(csv_text)
    except ValueError as e:
        if "Keine Prognosedaten gefunden" in str(e) or "CSV-Format" in str(e) or "CSV-Header" in str(e):
            print(f"Warnung: {e}. Sende Benachrichtigungs-E-Mail.")
            try:
                smtp_server, smtp_port, smtp_username, smtp_password, sender_email, recipients = load_smtp_config()
                sample = _build_diagnostic_sample(csv_text)
                body = (
                    "Automatische Hochwasserwarnung – Keine Prognosedaten verfügbar\n\n"
                    "Die Datenquelle liefert aktuell keine verwertbaren Prognosedaten.\n\n"
                    f"Fehlermeldung: {e}\n\n"
                    "Rohdaten-Snapshot der Datenquelle:\n"
                    "-----------------------------------\n"
                    f"{sample}\n"
                    "-----------------------------------\n\n"
                    f"Quelle: {FORECAST_URL}\n"
                )
                title = "Hochwasserwarnung – Keine Prognosedaten verfügbar (Atzenbrugg)"
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
                print("Benachrichtigungs-E-Mail gesendet.")
            except ValueError as smtp_err:
                print(f"Fehler beim Laden der SMTP-Konfiguration: {smtp_err}", file=sys.stderr)
                return 2
            return 0
        raise

    # Zeitraum ermitteln (erste und letzte Zeit in Prognose)
    start_ts, _ = forecast[0]
    end_ts, _ = forecast[-1]

    crossings = find_threshold_crossings(forecast, THRESHOLDS)

    # GitHub Step Summary schreiben (immer, auch wenn keine Warnung nötig ist)
    _write_github_summary(forecast, crossings)

    # Nur melden, wenn mindestens HQ1 erreicht wird
    hq1 = crossings.get("HQ1")
    if hq1 is None:
        print("Keine HQ1-Überschreitung in Prognose. Keine Aktion.")
        return 0

    body = build_issue_body(crossings, (start_ts, end_ts))
    title = "Hochwasserwarnung – Durchflussprognose HQ1+ erreicht (Atzenbrugg)"

    try:
        smtp_server, smtp_port, smtp_username, smtp_password, sender_email, recipients = load_smtp_config()
    except ValueError as e:
        print(f"Fehler beim Laden der SMTP-Konfiguration: {e}", file=sys.stderr)
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


