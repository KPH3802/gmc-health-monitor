#!/usr/bin/env python3
"""
GMC Health Monitor — 6:00 AM CT daily health check email.
Runs 6 checks and sends a single consolidated email before the trading day.
"""

import datetime
import email
import imaplib
import os
import smtplib
import socket
import sqlite3
import subprocess
import sys
from email.mime.text import MIMEText

import requests

import config

# ── Status constants ─────────────────────────────────────────────────────────

GREEN  = "GREEN"
YELLOW = "YELLOW"
RED    = "RED"

ICONS = {GREEN: "\u2705", YELLOW: "\u26a0\ufe0f", RED: "\u274c"}


# ── Individual checks ────────────────────────────────────────────────────────

def check_fmp_api():
    """CHECK 1 — FMP API Key validity."""
    try:
        url = f"https://financialmodelingprep.com/stable/economic-calendar?apikey={config.FMP_API_KEY}"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            resp.json()  # confirm valid JSON
            return GREEN, "OK"
        return RED, f"FMP API returned HTTP {resp.status_code}"
    except Exception as e:
        return RED, f"FMP API error: {e}"


# ── Studio IB Gateway probe config ───────────────────────────────────────────

STUDIO_GATEWAY_PORT = 4002                    # IB Gateway paper API port on the Studio
GATEWAY_EXPECTED_BY = datetime.time(7, 35)    # CT — Studio Gateway starts ~07:30
TCP_PROBE_TIMEOUT_S = 3


def _studio_host():
    """Resolve the Studio's address from the SAME source of truth gmc_watch uses:
    the SSH alias ``GMC_STUDIO_HOST`` (default ``studio``), resolved to a
    connectable HostName via ``~/.ssh/config``. No raw IP is hardcoded here."""
    alias = os.environ.get("GMC_STUDIO_HOST", "studio")
    try:
        out = subprocess.run(
            ["ssh", "-G", alias],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            if line.lower().startswith("hostname "):
                return line.split(None, 1)[1].strip()
    except Exception:
        pass
    return alias


def _tcp_reachable(host, port, timeout=TCP_PROBE_TIMEOUT_S):
    """True if a TCP connection to host:port completes within ``timeout``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_ib_gateway(now=None):
    """CHECK 2 — Studio IB Gateway reachable over the network.

    Network reachability probe to the Studio's paper API port (4002): the
    Gateway process runs on the Studio, not on this watchdog host, so a local
    process match is meaningless here. Before ~07:35 CT the Studio Gateway has
    not started yet (~07:30), so an unreachable port is expected (YELLOW) —
    mirroring the morning-brief pre-window; at/after that it is an error (RED).
    """
    now = now or datetime.datetime.now()
    host = _studio_host()
    try:
        if _tcp_reachable(host, STUDIO_GATEWAY_PORT):
            return GREEN, f"Reachable at {host}:{STUDIO_GATEWAY_PORT}"
        if now.time() < GATEWAY_EXPECTED_BY:
            return YELLOW, "Gateway not started yet \u2014 expected"
        return RED, (
            f"IB Gateway unreachable at {host}:{STUDIO_GATEWAY_PORT} "
            "\u2014 start/authenticate before 8AM"
        )
    except Exception as e:
        return RED, f"IB Gateway check error: {e}"


def check_smtp():
    """CHECK 3 — SMTP connection and auth."""
    try:
        server = smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=15)
        server.ehlo()
        server.starttls()
        server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
        server.quit()
        return GREEN, "Auth OK"
    except Exception as e:
        return RED, f"SMTP auth failed: {e}"


def check_positions_db():
    """CHECK 4 — Positions DB readable, count open positions."""
    try:
        if not os.path.exists(config.POSITIONS_DB):
            return RED, "positions.db not found"
        conn = sqlite3.connect(config.POSITIONS_DB)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM open_positions WHERE status='OPEN'")
        count = cur.fetchone()[0]
        conn.close()
        return GREEN, f"{count} open positions"
    except Exception as e:
        return RED, f"positions.db error: {e}"


def check_scanner_emails():
    """CHECK 5 — Scanner emails in last 48 hours via Gmail IMAP."""
    keywords = [
        "8-K", "PEAD Scanner", "SI SQUEEZE", "COT",
        "CEL Scanner", "Crypto Scanner", "Dividend Scanner",
    ]
    try:
        mail = imaplib.IMAP4_SSL(config.IMAP_HOST)
        mail.login(config.IMAP_USER, config.IMAP_PASSWORD)
        mail.select("INBOX", readonly=True)

        since_date = (datetime.datetime.now() - datetime.timedelta(hours=48)).strftime("%d-%b-%Y")
        _, msg_ids = mail.search(None, f'(SINCE "{since_date}")')

        count = 0
        if msg_ids[0]:
            for mid in msg_ids[0].split():
                _, data = mail.fetch(mid, "(BODY[HEADER.FIELDS (SUBJECT)])")
                raw_subject = data[0][1].decode("utf-8", errors="replace")
                msg = email.message_from_string(raw_subject)
                subject = msg.get("Subject", "")
                if any(kw.lower() in subject.lower() for kw in keywords):
                    count += 1

        mail.logout()

        if count >= 3:
            return GREEN, f"{count} scanner emails received"
        return RED, f"Only {count} scanner email(s) in 48h \u2014 check PA tasks"
    except Exception as e:
        return RED, f"IMAP check failed: {e}"


def check_morning_brief():
    """CHECK 6 — news_digest.log modified today."""
    try:
        log_path = config.NEWS_DIGEST_LOG
        if not os.path.exists(log_path):
            return YELLOW, "news_digest.log not found"
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(log_path))
        today = datetime.date.today()
        if mtime.date() == today:
            return GREEN, "Morning brief ran today"
        return YELLOW, "Morning brief has not run yet today"
    except Exception as e:
        return RED, f"Morning brief check error: {e}"


# ── Build and send email ─────────────────────────────────────────────────────

def run_all_checks():
    checks = [
        ("FMP API",        check_fmp_api),
        ("IB Gateway",     check_ib_gateway),
        ("SMTP",           check_smtp),
        ("Positions DB",   check_positions_db),
        ("Scanner Emails", check_scanner_emails),
        ("Morning Brief",  check_morning_brief),
    ]

    results = []
    for name, fn in checks:
        try:
            status, detail = fn()
        except Exception as e:
            status, detail = RED, f"Unexpected error: {e}"
        results.append((name, status, detail))
    return results


def build_email(results):
    now = datetime.datetime.now()
    date_str = now.strftime("%A %B %-d %Y")
    time_str = now.strftime("%-I:%M %p CT")

    reds = sum(1 for _, s, _ in results if s == RED)
    yellows = sum(1 for _, s, _ in results if s == YELLOW)

    # Subject line
    if reds == 0:
        subject = "[GMC HEALTH] All systems go"
    else:
        issue_word = "issue" if reds == 1 else "issues"
        subject = f"[GMC HEALTH] !! ATTENTION: {reds} {issue_word} found"

    # Body
    separator = "\u2501" * 42
    lines = [
        f"GMC HEALTH CHECK \u2014 {date_str} \u2014 {time_str}",
        separator,
    ]

    for name, status, detail in results:
        icon = ICONS[status]
        lines.append(f"{icon} {name:<16} {detail}")

    lines.append(separator)

    # Summary
    issues = []
    if yellows:
        issues.append(f"{yellows} warning{'s' if yellows != 1 else ''}")
    if reds:
        issues.append(f"{reds} error{'s' if reds != 1 else ''}")

    if issues:
        lines.append(f"Issues found: {', '.join(issues)}")
    else:
        lines.append("All checks passed.")

    lines.append("Next cron: 8:00 AM CT")
    lines.append("")

    return subject, "\n".join(lines)


def send_email(subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_SENDER
    msg["To"] = config.EMAIL_RECIPIENT

    server = smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=15)
    server.ehlo()
    server.starttls()
    server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
    server.sendmail(config.EMAIL_SENDER, config.EMAIL_RECIPIENT, msg.as_string())
    server.quit()


def main():
    print("GMC Health Monitor starting...")
    results = run_all_checks()
    subject, body = build_email(results)

    print(body)
    print(f"\nSubject: {subject}")

    try:
        send_email(subject, body)
        print("\nEmail sent successfully.")
    except Exception as e:
        print(f"\nFailed to send email: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
