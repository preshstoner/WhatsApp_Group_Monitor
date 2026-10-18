#!/usr/bin/env python3
"""
WhatsApp Group Monitor - FastAPI Service
Port 8086. Receives Evolution API webhooks, stores to PostgreSQL
"""

import os
import re
import json
import time
import traceback
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
import httpx
from typing import Optional, List
import asyncpg
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Query
from fastapi.responses import JSONResponse  ,PlainTextResponse
from pydantic import BaseModel, EmailStr

app = FastAPI(title="WhatsApp Group Monitor API")

# -- Logging Setup --
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wa_monitor")
logger = log #Creates an alias so both 'log' and 'logger' work everywhere


# -- Config (Customized for your environment) --
PG_USER = os.environ.get("POSTGRES_USER", "wa_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "wa_secure_password")

# If running locally on Windows, default to localhost. Docker will pass "postgres"
PG_HOST = os.environ.get("POSTGRES_HOST", "localhost") # <--- Changed from "localhost" to "postgres"
PG_PORT = os.environ.get("POSTGRES_PORT", "5432")
PG_DB = os.environ.get("POSTGRES_DB", "whatsapp_monitor")

# Make sure PG_DSN is assembled properly right below it:
PG_DSN = os.environ.get("PG_DSN", f"postgresql://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{PG_DB}")

EVOLUTION_URL = os.environ.get("EVOLUTION_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "NBASUPERSECRETKEY")
EVOLUTION_INSTANCE = "monitor-instance"


MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "./media"))


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_KEY_FILE = os.environ.get("ANTHROPIC_KEY_FILE", "./anthropic_key.txt")


# Resend / Email Config Fallbacks
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
FROM_NAME = os.environ.get("FROM_NAME", "WhatsApp Monitor")

WA_SYSTEM_PROMPT = """You are a WhatsApp group analysis assistant. You have access to WhatsApp group messages from staff groups. 

Your job: Summarize discussions, identify key topics, flag important items (decisions, deadlines, complaints, requests), and answer questions about group conversations. 

When analyzing messages:
- Group by topic/thread when summarizing
- Highlight action items and decisions
- Note who said what when relevant
- Flag urgent or important messages
- Be concise but thorough
- Use sender names when available, phone numbers when not
- Present information in a clear, structured format
- If messages are in a non-English language, provide analysis in English 

Format your response in markown."""


pool: asyncpg.Pool = None

# Track startup time and watchdog states for Prometheus
_startup_time = time.time()
_watchdog_stats = {
    "checks": 0,
    "last_check": None,
    "last_state": "unknown", # or 'connected' depending on your evolution API state
    "reconnects": 0
}


# -- Database Setup Helpers --
async def _init_tables():
    """Ensure wa_daily_summaries table exists."""
    async with pool.acquire() as conn:
        # Temporary drop line to reset the table schema:
        # await conn.execute("DROP TABLE IF EXISTS wa_scheduled_reports;")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS wa_daily_summaries (
                group_jid VARCHAR(255) NOT NULL,
                summary_date DATE NOT NULL,
                messsage_count INTEGER NOT NULL,
                summary TEXT NOT NULL,
                model VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (group_jid, summary_date)
            );

            CREATE TABLE IF NOT EXISTS wa_scheduled_reports(
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                report_type VARCHAR(50) NOT NULL, -- 'daily_summary', 'weekly_digest', 'activity_alert'
                recipients TEXT NOT NULL, -- comma-separated emails schedule_cron VARCHAR(50) DEFAULT '0 8 * * *',
                is_active BOOLEAN DEFAULT true,
                last_status_at TIMESTAMPTZ,
                last_status TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            ); 

            CREATE TABLE IF NOT EXISTS wa_settings (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)


# -- Lifespan Manager --
@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=10)
    log.info("PostgreSQL connection pool created successsfully.")

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    await _init_tables()
    log.info("Database tables verified.")

    yield

    if pool:
        await pool.close()
        log.info("PostgreSQL connection pool closed.")


# -- Pydantic Request Models --
class ReportCreate(BaseModel):
    name: str
    report_type: str # Daily_summary, weekly_digest, activity_alert
    recipients: str # comma-separated email addresses
    schedule_cron: Optional[str] = "0 8 * * *"
    is_active: Optional[bool] = True

class ReportUpdate(BaseModel):
    name: Optional[str] = None
    report_type: Optional[str] = None
    recipients: Optional[str] = None
    schedule_cron : Optional[str] = None
    is_active: Optional[bool] = None


class EmailConfigUpdate(BaseModel):
    resend_api_key: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = 587
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None


# -- Email Settings Helper --
async def _get_email_config() -> dict:
    """Load email settings from database or fallback to enviroment variables."""
    config = {
        "smtp_host": "resend" if RESEND_API_KEY else os.environ.get("SMTP_HOST", ""),
        "smtp_port": int(os.environ.get("SMTP_PORT", 587)),
        "smtp_user": os.environ.get("SMTP_USER", ""),
        "smtp_pass": RESEND_API_KEY or os.environ.get("SMTP_PASS", ""), 
        "from_email": FROM_EMAIL,
        "from_name": FROM_NAME,
        "smtp_tls": True
    }

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM wa_settings")
        db_settings = {r["key"]: r["value"] for r in rows}

    if db_settings.get("resend_api_key"):
        config["smtp_host"] = "resend"
        config["smtp_pass"] = db_settings["resend_api_key"]
    elif db_settings.get("smtp_host"):
        config["smtp_host"] = db_settings["smtp_host"]
        config["smtp_port"] = int(db_settings.get("smtp_port", 587))
        config["smtp_user"] = db_settings.get("smtp_user", "")
        config["smtp_pass"] = db_settings.get("smtp_pass", "")

    if db_settings.get("from_email"):
        config["from_email"] = db_settings["from_email"]
    if db_settings.get("from_name"):
        config["from_name"] = db_settings["from_name"]

    return config


# -- Synchronous Email Delivery Function (Resend API & SMTP) --
def _send_email_sync(config: dict, to_list: list, subject: str, html_body: str) -> str:
    """Send email via Resend API or SMTP. Returns empty string on success, error message on failure."""
    import json
    import re
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    # -- Resend API Mode --
    if config.get("smtp_host", "").lower() == "resend":
        api_key = config.get("smtp_pass", "")
        if not api_key:
            return "Resend API key not configured"

        from_str = f"{config.get('from_name', 'WhatsApp Monitor')} <{config.get('from_email', 'onboarding@resend.dev')}>"

        # Plain text fallback
        plain = re.sub(r'<[^>]+>', '', html_body)
        plain = re.sub(r'\s+', '', plain).strip()

        payload = json.dumps({
            "from": from_str,
            "to": to_list,
            "subject": subject,
            "html": html_body,
            "text": plain,
        }).encode()

        req = Request("https://api.resend.com/emails", data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        # ADDED: User-Agent header to prevent Resend / Cloudflare 403 (Error 1010)
        req.add_header("User-Agent", "WhatsAppMonitor/1.0")

        try:
            resp = urlopen(req, timeout=30)
            data = json.loads(resp.read())
            return "" if data.get("id") else f"Resend error: {data}"
        except HTTPError as e:
            body = e.read().decode()
            return f"Resend HTTP {e.code}: {body}"
        except Exception as e:
            return f"Resend error: {e}"


    # -- Traditional SMTP Mode --
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not config.get("smtp_host"):
        return "Email provider not configured (neither Resend API Key nor SMTP Host provided)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{config.get('from_name')} <{config.get('from_email')}>"
    msg["To"] = ", ".join(to_list)

    plain = re.sub(r'<[^>]+>', '', html_body)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))


    try:
        if config.get("smtp_tls", True):
            server = smtplib.SMTP(config["smtp_host"], config.get("smtp_port", 587), timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP(config["smtp_host"], config.get("smtp_port", 25), timeout=30)

        if config.get("smtp_user") and config.get("smtp_pass"):
            server.login(config["smtp_user"], config["smtp_pass"])

        server.sendmail(msg["From"], to_list, msg.as_string())
        server.quit()
        return ""
    except Exception as e:
        return f"SMTP Error: {e}"


# --HTML Email Template Builder --
def _build_email_html(title: str, sections:list) -> str:
    """Build HTML email layout. sections = [(heading, content_html), ...]"""
    sections_html = ""
    for heading, content in sections:
        sections_html += f'''
        <tr><td style="padding:0 24px 20px;">
            <h2 style="margin:0 0 8px;font-size:14px;color:#25D366;font-family:'Courier New', monospace;border-bottom:1px solid #2a2a2e;padding-bottom:6px;">{heading}</h2>
            <div style="font-size:13px;line-height:1.7;color:#d4d4d8;">{content}</div>
        </td></tr>'''


    return f'''<!DOCTYPE html><html><head>
    <meta charset="utf-8"></head>
    <body style="margin:0;padding:0;background:#0a0a0b;font-family:-apple-system,BlinkMacSystemFont, 'Segoe UI', Roboto,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0b;padding:20px 0; ">
    <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0" style="background:#111113;border:1px solid #27272a;border-radius:8px;overflow:hidden;">
        <tr><td style="background:#18181b;padding:16px 24px;border-bottom:1px solid #27272a;">
            <span style="font-family:'Courier New', monospace;font-size:15px;font-weight:bold;color:#e4e4e7;">
                <span style="color:#25D366;">&#9679;</span> {title}
            </span>
        </td></tr>
        {sections_html}
        <tr><td style="padding:16px 24px;border-top:1px solid #27272a;text-align:center;">
            <span style="font-size:10px;color:#52525b;font-family:'Courier New',monospace;">
                WhatsApp Monitor -- Automated Report
            </span>
        </td><tr>
    </table>
    </td></tr></table>
    </body></html>'''


# -- Report Generation Engine --
async def _send_single_report(report:dict) -> dict:
    """Dispatches a single automated report via Resend API."""
    api_key = report.get("api_key") or os.getenv("RESEND_API_KEY")
# Fallback tp your test key
    sender = report.get("sender_email") or "onboarding@resend.dev"


    recipient = report.get("recipient_email") or report.get("recipients")
    
    report_name = report.get("name", "WhatsApp Summary Report")

    if not recipient:
        return {"status": "error", "message": "No recipient email configured"}

    payload = {
        "from": sender,
        "to": [recipient],
        "subject": f"Automated Report: {report_name}",
        "html": f"<h3>{report_name}</h3><p>Here is your scheduled WhatsApp activity summary and AI insights.</p>"
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("https://api.resend.com/emails", json=payload, headers=headers)
            if response.status_code >= 200 and response.status_code < 300:
                # Update last_sent_at in database
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE wa_scheduled_reports SET last_sent_at = NOW(), last_status = 'success' WHERE id = $1",
                        report["id"]
                    )
                return {"status": "success", "resend_response": response.json()}
            else:
                return {"status": "error", "code": response.status_code, "detail": response.text}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    """Generate and send a single report."""
    try:
        config = await _get_email_config()
        if not config.get("smtp_host"):
            return{"status": "error", "detail": "Email provider not configured"}

        recipients = [r.strip() for r in report["recipients"].split(",") if r.strip()]
        if not recipients:
            return {"status": "error", "detail": "No valid recipients specified"}

        report_type = report["report_type"]
        sections = []
        subject = ""
        title = ""

        async with pool.acquire() as conn:
            groups = await conn.fetch("SELECT jid, name FROM wa_groups WHERE is_monitored = true")

            # 1. Daily Summary
            if report_type == "daily_summary":
                yesterday = (date.today() - timedelta(days=1)).isoformat()
                subject = f"WhatsApp Daily Summary -- {yesterday}"
                title = f"Daily Summary -- {yesterday}"

                for g in groups:
                    # FIX 1: Fixed SQL column name 'group_jid'
                    summary = await conn.fetchrow(
                        """SELECT summary, message_count FROM wa_daily_summaries WHERE group_jid = $1 AND summary_date = $2""",
                        g["jid"], date.fromisoformat(yesterday)
                    )

                    if summary and summary["message_count"] > 0:
                        content = summary["summary"].replace("\n", "<br>")
                        content = f'<p style="color:#a1a1aa;margin-top:0;">{summary["message_count"]} messages analyzed</p>' + content
                        sections.append((g["name"] or g["jid"], content))

                if not sections:
                    sections.append(("Status", "No message activity recored for yesterday across monitored groups."))

            # 2. Weekly Digest
            elif report_type == "weekly_digest":
                start_date = date.today() - timedelta(days=7)
                subject = f"WhatsApp Weekly Digest -- {start_date} to {date.today()}"
                title = f"Weekly Digest ({start_date} to {date.today()})"

                stats = await conn.fetchrow("""
                    SELECT COUNT(*) as total_msgs, COUNT(DISTINCT sender_phone) as unique_senders, COUNT(DISTINCT group_jid) as active_groups FROM wa_messages WHERE timestamp >= NOW() - INTERVAL '7 days'
                """)

                # FIX 2: Fixed dictionary key 'total_msgs'
                overview_content = f"""
                <ul style="margin:0;padding-left:18px;color:#d4d4d8;">
                    <li><b>Total Messages:</b> {stats['total_msgs'] or 0}</li>
                    <li><b>Active Participants:</b> {stats['unique_senders'] or 0}</li>
                    <li><b>Active Groups:</b> {stats['active_groups'] or 0}</li>
                </ul>
                """
                sections.append(("7-Day Activity Overview", overview_content))


                # Top Senders
                top_senders = await conn.fetch("""
                    SELECT COALESCE(NULLIF(sender_name, ''), sender_phone) as sender, COUNT(*) as msg_cnt
                    FROM wa_messages
                    WHERE timestamp >= NOW() - INTERVAL '7 days'
                    GROUP BY sender
                    ORDER BY msg_cnt DESC LIMIT 5
                """)
                if top_senders:
                    sender_rows = "".join([f"<li><b>{s['sender']}:</b>{s['msg_cnt']} msgs</li>" for s in top_senders])
                    sections.append(("Top Contributors", f'<ul style="margin:0;padding-left:18px;">{sender_rows}</ul>'))

            # 3. Activity Alert
            elif report_type == "activity_alert":
                today = date.today().isoformat()
                subject = f"WhatsApp Activity Alert -- {today}"
                title = f"Group Activity Check -- {today}"

                active_list = []
                silent_list = []

                for g in groups:
                    msg_count = await conn.fetchval(
                        "SELECT COUNT(*) FROM wa_messages WHERE group_jid = $1 AND timestamp::date = $2", g["jid"], date.today()
                    )
                    g_name = g["name"] or g["jid"]
                    if msg_count > 0:
                        active_list.append(f"<li><b>{g_name}:</b> {msg_count} messages today</li>")
                    else:
                        silent_list.append(f"<li><span style='color:#ef4444;'>&#9888;</span> <b>{g_name}</b> (0 messages today)</li>")
                if silent_list:
                    sections.append(("Disengaged / Silent Groups Today", f'<ul style="margin:0;padding-left:18px;">{"".join(silent_list)}</ul>'))
                if active_list:
                    sections.append(("Active Groups Today", f'<ul style="margin:0;padding-left:18px;">{"".join(active_list)}</ul>'))

            else:
                return {"status": "error", "detail": f"Unknown report_type: {report_type}"}

        html = _build_email_html(title, sections)

        # Dispatch email in worker thread
        loop = asyncio.get_running_loop()
        err = await loop.run_in_executor(None, _send_email_sync, config, recipients, subject, html)

        status = "success" if not err else f"failed: {err}"

        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE wa_scheduled_reports SET last_sent_at = NOW(), last_status = $2 WHERE id = $1""",
                report["id"], status
            )

        if err:
            return {"status": "error", "detail": str(err)}
        return {"status": "ok", "sections": len(sections), "recipients": len(recipients)}

    except Exception as e:
        error_str = f"{type(e).__name__}: {str(e)}"
        log.error(f"Error executing report #{report.get('id')}: {error_str}", exc_info=True)
        return {"status": "error", "detail": error_str}


# -- REST API: Settings Management --
@app.get("/email-config")
async def get_email_configuration():
    """Retrieve current email delivery mode and settings."""
    config = await _get_email_config()
    # Mask API key/password
    if config.get("smtp_pass"):
        config["smtp_pass"] = config["smtp_pass"][:6] + "..."
    return config



@app.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    """Prometheus-compatible metrics for Grafana."""
    lines = []
    try: 
        # 1. Service Status & Uptime
        lines.append("# HELP wa_monitor_up Service status")
        lines.append("# TYPE wa_monitor_up gauge")
        wa_up = 1
        lines.append(f"wa_monitor_up {wa_up}")

        lines.append("# HELP wa_monitor_uptime_seconds Uptime")
        lines.append("# TYPE wa_monitor_uptime_seconds gauge")
        startup_time = globals().get("_startup_time", time.time())
        lines.append(f"wa_monitor_uptime_seconds {int(time.time() - _startup_time)}")

        lines.append("# HELP wa_monitor_reconnects_total Reconnects")
        lines.append("# TYPE wa_monitor_reconnects_total counter")
        reconnects = -_watchdog_stats.get("reconnects", 0) if '_watchdog_stats' in globals() else 0
        lines.append(f"wa_monitor_reconnects_total {reconnects}")

        # 2. Database Metrics
        today = 0
        total = 0
        groups = 0

        if 'pool' in globals() and pool is not None:
            async with pool.acquire() as conn:
                today = await conn.fetchval(
                    "SELECT COUNT(*) FROM wa_messages WHERE timestamp >= CURRENT_DATE") or 0
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM wa_messages") or 0
                groups = await conn.fetchval(
                    "SELECT COUNT(*) FROM wa_groups WHERE is_monitored = true") or 0

        lines.append(f"wa_monitor_messages_today {today}")
        lines.append(f"wa_monitor_messages_total {total}")
        lines.append(f"wa_monitor_groups {groups}")

    except Exception as e:
        # Fallback metric line if DB or variable fail, ensuring it never returns null
        lines.append(f"# ERROR Encountered error generating metrics:{str(e)}")
        lines.append("wa_monitor_up 0")

    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4"
    )

@app.get("/watchdog/stats")
async def get_watchdog_stats():
    """Return current connection watchdog stats and health."""
    return _watchdog_stats

@app.post("/email-config")
async def update_email_configuration(payload: EmailConfigUpdate):
    """Save Resend or SMTP credentials to wa_settings."""
    async with pool.acquire() as conn:
        if payload.resend_api_key is not None:
            await conn.execute(
                "INSERT INTO wa_settings (key, value) VALUES ('resend_api_key', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                payload.resend_api_key
            )
        if payload.from_email is not None:
            await conn.execute(
                "INSERT INTO wa_settings (key, value) VALUES ('from_email', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                payload.from_email
            )
        if payload.from_name is not None:
            await conn.execute(
                "INSERT INTO wa_settings (key, value) VALUES ('from_name', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                payload.from_name
            )
        if payload.smtp_host is not None:
            await conn.execute(
                "INSERT INTO wa_settings (key, value) VALUES ('smtp_host', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                payload.smtp_host
            )
        if payload.smtp_user is not None:
            await conn.execute(
                "INSERT INTO wa_settings (key, value) VALUES ('smtp_user', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                payload.smtp_user
            )

        if payload.smtp_pass is not None:
            await conn.execute(
                "INSERT INTO wa_settings (key, value) VALUES ('smtp_pass', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                payload.smtp_pass
            )
    return {"status": "success", "message": "Email settings updated successfully"}


# -- REST API: Report CRUD & Trigger Endpoints --
@app.get("/reports")
async def list_reports():
    """List all scheduled reports."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM wa_scheduled_reports ORDER BY id ASC")
        return [dict(r) for r in rows]


@app.post("/reports")
async def create_report(payload: ReportCreate):
    """Create a new scheduled report."""
    if payload.report_type not in ("daily_summary", "weekly_digest", "activity_alert"):
        raise HTTPException(status_code=400, detail="Invalid report_type")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wa_scheduled_reports (name, report_type, recipients, schedule_cron, is_active) VALUES ($1, $2, $3, $4, $5) RETURNING *""",
            payload.name, payload.report_type, payload.recipients, payload.schedule_cron, payload.is_active
        )
        return dict(row)

@app.put("/reports/{report_id}")
async def update_report(report_id: int, payload:ReportUpdate):
    """Update an existing report configuration."""
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT * FROM wa_scheduled_reports WHERE id = $1", report_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Report not found")

        name = payload.name if payload.name is not None else existing["name"]
        rtype = payload.report_type if payload.report_type is not None else existing["report_type"]
        recipients = payload.recipients if payload.recipients is not None else existing["recipients"]
        cron = payload.schedule_cron if payload.schedule_cron is not None else existing["schedule_cron"]
        active = payload.is_active if payload.is_active is not None else existing["is_active"]

        row = await conn.fetchrow(
            """UPDATE wa_scheduled_reports SET name = $1, report_type = $2, recipients = $3, schedule_cron = $4, is_active = $5 WHERE id = $6 RETURNING *""",
            name, rtype, recipients, cron, active, report_id
        )
        return dict(row)

@app.delete("/reports/{report_id}")
async def delete_report(report_id: int):
    """Delete a report configuration."""
    async with pool.acquire() as conn:
        res = await conn.execute("DELETE FROM wa_scheduled_reports WHERE id = $1", report_id)
        if res == "DELETE 0":
            raise HTTPException(status_code=404, detail="Report not found")
        return {"status": "success", "message": f"Report {report_id} deleted"}


@app.post("/reports/{report_id}/send")
async def send_report_now(report_id: int):
    """Trigger immediate generation and delivery of a report."""
    async with pool.acquire() as conn:
        report = await conn.fetchrow("SELECT * FROM wa_scheduled_reports WHERE id = $1", report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")

    result = await _send_single_report(dict(report))
    if result.get("status") == "error":
        # Returns the detailed exception string directly in the HTTP 500 response body
        raise HTTPException(status_code=500, detail=result.get("detail"))
    
    return result


@app.post("/cron/send-reports")
async def process_due_reports():
    """Trigger check and execution of all active scheduled reports."""
    async with pool.acquire() as conn:
        reports = await conn.fetch("SELECT * FROM wa_scheduled_reports WHERE is_active = true")

    results = []
    for r in reports:
        res = await _send_single_report(dict(r))
        results.append({"report_id": r["id"], "name": r["name"], "result": res})

    return {"processed": len(results), "details": results}


@app.post("/cron/send-reports")
async def cron_send_reports():
    """Check and send any due reports."""
    now = datetime.utcnow()
    results = []

    async with pool.acquire() as conn:
        # Fetch active reports (ensure your wa_scheduled_reports table exists)
        reports = await conn.fetch(
            "SELECT * FROM wa_scheduled_reports"
            "WHERE is_active = true"
        )

    for r in reports:
        report = dict(r)
        last_sent = report.get("last_sent_at")

        # Already sent today? Skip.
        if last_sent and last_sent.date() == now.date():
            continue

        # Parse schedule time (e.g., "08:00")
        sched_h, sched_m = map(int, 
            report.get("schedule_time", "08:00").split(":"))

        # Check 15-mintues window
        now_min = now.hour * 60 + now.minute
        sched_min = sched_h * 60 + sched_m
        if now_min < sched_min or now_min > sched_min + 15:
            continue

        # Check schedule type
        should_send = False
        if report["schedule"] == "daily":
            should_send = True
        elif report["schedule"] == "weekly":
            should_send = (now.isoweekday() == report["schedule_day"])
        elif report["schedule"] == "monthly":
            should_send = (now.day == report["schedule_day"])

        if should_send:
            result = await _send_single_report(report)
            results.append({"report": report["name"], **results})

    return {"sent": results}

@app.get("/groups")
async def get_monitored_groups():
    """Fetch all monitored WhatsApp groups from the database."""
    try:
        # Assuming you have a connection pool or database session available, adjust this query to match your database schema (e.g., a 'groups' or 'messages' table)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT jid, name, updated_at FROM wa_groups ORDER BY updated_at DESC"
            )
            return [dict(row) for row in rows]
    except Exception as e:
        # Fallback if table name differs or schema is slightly named differently
        return {"groups": [], "warning": str(e)}


@app.get("/messages")
async def get_messages(jid: str):
    async with pool.acquire() as conn:
        # Adjust table name if you use wa_messages or a partitioned view
        rows = await conn.fetch(
            """
            SELECT COALESCE(NULLIF(sender_name, ''), sender_phone) AS sender, content AS message_text, timestamp
            FROM wa_messages
            WHERE group_jid = $1
            ORDER BY timestamp ASC
            LIMIT 100
            """,
            jid
        )
    return [dict(row) for row in rows]

# -- Anthropic Claude Helper Functions --
def _get_api_key() -> str:
    """Read Anthropic API key from enviroment or file."""
    if ANTHROPIC_API_KEY:
        return ANTHROPIC_API_KEY

    key_file = Path(ANTHROPIC_KEY_FILE)
    if key_file.exists():
        key = key_file.read_text().strip()
        if key:
            return key

    raise HTTPException(
        status_code=500,
        detail="Anthropic API key not configured. Set ANTHROPIC_API_KEY env var or create anthropic_key.txt"
    )

@app.post("/sync")
async def sync_groups():
    # Point to Evolution API inside Docker network or locally
    evolution_url = os.environ.get("EVOLUTION_API_URL", "http://evolution-api:8080")
    api_key = os.environ.get("EVOLUTION_API_KEY", "NBASUPERSECRETKEY")
    instance = "monitor-instance"

    headers = {"apikey": api_key}

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            target = f"{evolution_url}/group/fetchAllGroups/{instance}?getParticipants=false"
            # Fetch groups from Evolution API
            resp = await client.get(
                target,
                headers=headers
            )
            if resp.status_code != 200:
                return {"error": "Failed to fetch from Evolution API", "details": resp.text}

            groups_data = resp.json()
        except Exception as e:
            return {"error": f"Evolution API connection failed: {repr(e)}"}

    inserted = 0
    async with pool.acquire() as conn:
        for g in groups_data:
            # Adjust column names below to match your \d wa_groups output if needed
            group_jid = g.get("id")
            name = g.get("subject", "Unnamed Group")
            desc = g.get("desc", "")

            await conn.execute(
                """
                INSERT INTO wa_groups (jid, name, description, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (jid) DO UPDATE
                SET name = EXCLUDED.name, description = EXCLUDED.description, updated_at =NOW()
                """,
                group_jid, name, desc
            )
            inserted += 1

    return {"status": "success", "message": f"Successfully synced {inserted} groups into wa_groups. "}

async def _call_claude(messages: list, model: str = "claude-haiku-4-5-20251001",
                       max_tokens: int = 2000, system: str = "") -> str:
    """Async call to Anthropic Claude API"""
    api_key = _get_api_key()
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        body["system"] = system

    headers = {
        "x-api-key": api_key,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body
            )
            if resp.status_code != 200:
                err = resp.json().get("error", {}).get("message", resp.text[:300])
                raise HTTPException(status_code=resp.status_code, detail=f"Claude API Error: {err}")

            return resp.json()["content"][0]["text"].strip()

    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach Claude API: {exc}")


# -- Database Initialization & Lifespan --
@app.on_event("startup")
async def startup_db():
    global pool
    pool = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=10)
    log.info("PostgresSQL connection pool created successfully.")

    # Ensure media directory exists
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

@app.on_event("startup")
async def startup_event():
    # Start the background watchdog loop as a non-blocking task
    asyncio.create_task(_watchdog_loop())
    log.info("Background watchdog task initialized.")


@app.on_event("shutdown")
async def shutdown_db():
    global pool
    if pool:
        await pool.close()
        log.info("PostgresSQL connection pool closed.")


# -- Group and Participant Tracking Helpers --
async def _ensure_group(jid: str, name: str = ""):
    """Insert group if not exists."""
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO wa_groups (jid, name)
            VALUES ($1, $2)
            ON CONFLICT (jid) DO NOTHING
        """, jid, name)

async def _upsert_participant(group_jid: str, phone: str, push_name: str = ""):
    """Track participant activity."""
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO wa_participants
                (group_jid, phone, push_name, last_seen)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (group_jid, phone) DO UPDATE SET
                push_name = CASE
                    WHEN EXCLUDED.push_name != '' THEN EXCLUDED.push_name
                    ELSE wa_participants.push_name
                END,
                last_seen = NOW()
        """, group_jid, phone, push_name)


# -- Media Download and Background Processing --
async def _download_and_process_media(instance, data, msg_id, group_jid, media_type, media_mime, ts):
    """Download media via Evolution API and save locally."""
    try:
        key = data.get("key", {})
        message = data.get("message", {})

        # Date-based directory structure
        date_dir = MEDIA_DIR / group_jid / ts.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        ext_map = {
            "image/jpeg": ".jpg", 
            "image/png": ".png", 
            "image/webp": ".webp", 
            "audio/ogg": ".ogg", 
            "audio/ogg; codecs=opus": ".ogg", 
            "audio/mp4": ".m4a", 
            "video/mp4": ".mp4",
            "application/pdf": ".pdf", 
        }
        ext = ext_map.get(media_mime, ".bin")
        media_path = date_dir / f"{msg_id}{ext}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{EVOLUTION_URL}/chat/getBased64FromMediaMessage/{instance}",
                json={"message": {"key": key, "message": message}},
                headers={"apikey": EVOLUTION_API_KEY}
            )
            if resp.status_code != 200:
                log.warning(f"Media download failed: {msg_id}")
                return

            import base64
            b64_data = resp.json().get("base64", "")
            if not b64_data:
                return
            raw = base64.b64decode(b64_data)
            media_path.write_bytes(raw)

        # Update database record
        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE wa_messages
                SET media_path = $1, media_size = $2
                WHERE id = $3 AND timestamp = $4
            """, str(media_path), len(raw), msg_id, ts) 

        log.info(f"Media saved: {msg_id} ({len(raw)} bytes)")

    except Exception as e:
        log.error(f"Media processing failed for {msg_id}: {e}")


# -- Core Message Processing Pipeline -- 
async def _process_message(instance: str, data: dict):
    """Parse an incoming message payload and store it in PostgresSQL."""
    key = data.get("key", {})
    msg_id = key.get("id", "")
    remote_jid = key.get("remoteJid", "")

    # Only process group messages (JIDs ending in @g.us)
    if not remote_jid.endswith("@g.us"):
        return


    # Extract sender details
    participant = key.get("participant", "") or data.get("participant", "")
    sender_phone = participant.split("@")[0] if participant else ""
    sender_name = data.get("pushName", "")


    # Extract content & type
    message = data.get("message", {})
    msg_type = "conversation"
    content = ""
    media_type = None
    media_mime = None

    if "conversation" in message:
        content = message["conversation"]
        msg_type = "conversation"
    elif "extendedTextMessage" in message:
        content = message["extendedTextMessage"].get("text", "")
        msg_type = "extendedText"
    elif "imageMessage" in message:
        msg_type = "image"
        media_type = "image"
        content = message["imageMessage"].get("caption", "")
        media_mime = message["imageMessage"].get("mimetype", "image/jpeg")
    elif "audioMessage" in message:
        msg_type = 'audio'
        media_type = "audio"
        media_mime = message["audioMessage"].get("mimetype", "audio/ogg")
    elif "videoMessage" in message:
        msg_type = "video"
        media_type = "video"
        content = message["videoMessage"].get("caption", "")
        media_mime = message["videoMessage"].get("mimetype", "video/mp4")
    elif "documentMessage" in message:
        msg_type = "document"
        media_type = "document"
        fname = message["documentMessage"].get("fileName", "")
        content = f"[Document: {fname}]" if fname else "[Document]"
        media_mime = message["documentMessage"].get("mimetype", "")
    elif "stickerMessage" in message:
        msg_type = "sticker"
        content = "[Sticker]"
    elif "reactionMessage" in message:
        msg_type = "reaction"
        content = message["reactionMessage"].get("text", "")
    elif "contactMessage" in message:
        msg_type = "contact"
        display = message["contactMessage"].get("displayName", "")
        content = f"[Contact: {display}]"
    elif "locationMessage" in message:
        msg_type = "location"
        lat = message["locationMessage"].get("degreesLatitude", "")
        lon = message["locationMessage"].get("degreesLongitude", "")
        content = f"[Location: {lat}, {lon}]"
    else:
        msg_type = list(message.keys())[0] if message else "unknown"

    # Timezone-aware timestamp handling (seconds vs milliseconds check)
    ts_epoch = data.get("messageTimestamp", 0)
    if isinstance(ts_epoch, str):
        ts_epoch = int(ts_epoch)
    if ts_epoch > 10_000_000_000: # Handle millisecond timestamps
        ts_epoch //= 1000

    ts = (datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
          if ts_epoch else datetime.now(timezone.utc))

    # Extract reply reference context
    reply_to = None
    for msg_key in ("extendedTextMessage", "imageMessage", "videoMessage", "documentMessage", "audioMessage"):
        ctx = message.get(msg_key, {}).get("contextInfo", {})
        if ctx:
            reply_to = ctx.get("stanzaId")
            break

    # Ensure group record exists
    await _ensure_group(remote_jid)

    # Insert message (idempotent via ON CONFLICT)
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO wa_messages
                (id, group_jid, sender_phone, sender_name, message_type, content, media_type, media_mime, reply_to_id, timestamp, raw_payload)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (id, timestamp) DO NOTHING
        """, msg_id, remote_jid, sender_phone, sender_name, msg_type, content, media_type, media_mime, reply_to, ts, json.dumps(data))

        
    # Upsert participant activity
    if sender_phone: 
        await _upsert_participant(remote_jid, sender_phone, sender_name)

    # Trigger async media download in background
    if media_type in ("image", "audio", "video", "document"):
        asyncio.create_task(_download_and_process_media(
            instance, data, msg_id, remote_jid, media_type, media_mime, ts
        ))

    log.info(f"Stored messsage: {msg_id} in {remote_jid} from {sender_phone} ({msg_type})")


async def _ensure_partitions(conn):
    """Create partitions for current month + next 3 months."""
    now = datetime.now(timezone.utc)
    for i in range(4):
        m = now.month + i
        y = now.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        nm = m + 1
        ny = y + (nm - 1) // 12
        nm = ((nm - 1) % 12) + 1

        part_name = f"wa_messages_{y}_{m:02d}"
        start = f"{y}-{m:02d}-01"
        end = f"{ny}-{nm:02d}-01"

        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {part_name} 
            PARTITION OF wa_messages
            FOR VALUES FROM ('{start}') TO ('{end}');
        """)

async def _watchdog_loop():
    """Background watchdog: auto-reconnect, sync groups, cleanup."""
    await asyncio.sleep(10) # Let startup finish
    log.info("Watchdog started")

    while True:
        try:
            _watchdog_stats["checks"] += 1
            _watchdog_stats["last_check"] = datetime.utcnow().isoformat()

            # 1. Check WhatsApp connection state via Evolution API
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{EVOLUTION_URL}/instance/connectionState/{EVOLUTION_INSTANCE}",
                    headers={"apikey": EVOLUTION_API_KEY}
                )

                if resp.status_code == 200:
                    state = resp.json().get("instance", {}).get("state", "unknown")
                    _watchdog_stats["last_state"] = state

                    if state in ("close", "connecting"):
                        # Disconnected -- attempt reconnect
                        log.warning(f"WhatsApp disconnected (state={state}), reconnecting...")
                        await client.get(
                            f"{EVOLUTION_URL}/instance/connect/{EVOLUTION_INSTANCE}",
                            headers={"apikey": EVOLUTION_API_KEY}
                        )
                        _watchdog_stats["reconnects"] += 1

                elif resp.status_code == 404:
                    # Instance was deleted -- recreate it with correct port 8000
                    log.warning("Instance missing, recreating...")
                    await client.post(
                        f"{EVOLUTION_URL}/instance/create",
                        headers={
                            "apikey": EVOLUTION_API_KEY,
                            "Content-Type": "application/json"
                        },
                        json={
                            "instanceName": EVOLUTION_INSTANCE,
                            "intergration": "WHATSAPP-BAILEYS",
                            "qrcode": True,
                            "webhook": {
                                "url": "http://wa_fastapi:8000/webhook/evolution",
                                "byEvents": False,
                                "base64": True,
                                "events": [
                                    "MESSAGES_UPSERT",
                                    "GROUPS_UPSERT",
                                    "GROUP_PARTICIPANTS_UPDATE",
                                    "CONNECTION_UPDATE",
                                    "QRCODE_UPDATED"
                                ]
                            }
                        }
                    )
                    _watchdog_stats["reconnects"] += 1

            # 2. Webhook log cleanup (30-day retention)
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM wa_webhook_log "
                    "WHERE created_at < NOW() - INTERVAL '30 days'"
                )

        except asyncio.CancelledError:
            return
        except Exception as e:
            log.error(f"Watchdog error: {e}")

        await asyncio.sleep(60)


@app.post("/webhook/evolution")
async def webhook_evolution(request: Request, background_tasks: BackgroundTasks):
    """Webhook endpoint receiving payloads from Evolution API."""
    payload = await request.json()
    event = payload.get("event")
    instance = payload.get("instance", "default")
    data = payload.get("data", {})

    if event in ("messages.upsert", "SEND_MESSAGE"):
        background_tasks.add_task(_process_message, instance, data)

    return JSONResponse(content={"status": "success"})

# -- AI Summary Endpoint --
@app.post("/summarize/{jid}")
async def summarize_group(jid: str, target_date: str = ""):
    """Generate or retrieve a daily summary for a group"""
    if not target_date:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    try:
        summary_date = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Check cache first
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """SELECT summary, message_count, model FROM wa_daily_summaries WHERE group_jid = $1 AND summary_date = $2""",
            jid, summary_date
        )
        if existing:
            return {
                "summary": existing["summary"],
                "message_count": existing["message_count"],
                "cached": True,
            }

        # Fetch messages for the target date
        messages = await conn.fetch("""
            SELECT sender_name, sender_phone, content, message_type, timestamp FROM wa_messages WHERE group_jid = $1 AND timestamp::date = $2 ORDER BY timestamp ASC
        """, jid, summary_date)

        if not messages:
            return {
                "summary": "No messages found on this date.",
                "message_count": 0,
                "cached": False
            }

        group = await conn.fetchrow("SELECT name FROM wa_groups WHERE jid = $1", jid)
        group_name = group["name"] if (group and group["name"]) else jid

    # Format message log prompt
    msg_text = []
    for m in messages:
        name = m["sender_name"] or m["sender_phone"] or "Unknown"
        ts = m["timestamp"].strftime("%H:%M")
        content = m["content"] or f"[{m['message_type']}]"
        msg_text.append(f"[{ts}] {name}: {content}")

    prompt = f"""Summarize the following WhatsApp group messages from "{group_name}" on {target_date}.
    
    Group by topic/thread. Highlight any decisions, action items, or important announcements.
    Be concise (2-4 paragraphs max). If messages are in a non-English language, summarize in English.

    Messages:
    {chr(10).join(msg_text)}"""

    summary = await _call_claude(
        [{"role": "user", "content": prompt}],
        model="claude-haiku-4-5-20251001",
        max_tokens=1000
    )

    # Save summary in cache
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO wa_daily_summaries
                (groud_jid, summary_date, message_count, summary, model)
            VALUES ($1, $2, $3, $4, 'haiku')
            ON CONFLICT (group_jid, summary_date) DO UPDATE SET
                summary = EXCLUDED.summary,
                message_count = EXCLUDED.message_count
        """, jid, summary_date, len(messages), summary)


    return {
        "summary": summary,
        "message_count": len(messages),
        "cached": False
    }


# -- Interactive AI Q&A Endpoint --
@app.post("/ask")
async def ask_ai(payload: dict):
    """AI analysis endpoint -- answers questions about recent group messages."""
    question = payload.get("question", "").strip()
    group_jid = payload.get("group_jid", "")

    if not question:
        raise HTTPException(status_code=400, detail="A question field is required.")

    context_parts = []
    async with pool.acquire() as conn:
        if group_jid:
            messages = await conn.fetch("""
                SELECT sender_name, sender_phone, content, message_type, timestamp
                FROM wa_messages
                WHERE group_jid = $1 AND timestamp >= CURRENT_DATE - INTERVAL '7 days' ORDER BY timestamp ASC LIMIT 500
            """, group_jid)

            for m in messages:
                name = m["sender_name"] or m["sender_phone"] or "Unknown"
                ts = m["timestamp"].strfttime("%Y-%m-%d %H:%M")
                content = m["content"] or f"[{m['message_type']}]"
                context_parts.append(f"[{ts}] {name}: {content}")

    context = "\n".join(context_parts) if context_parts else "No recent message context found."
    user_msg = (f"Here is the WhatsApp message data:\n\n{context}"
                f"\n\n---\n\nQuestion: {question}")

    answer = await _call_claude(
        [{"role": "user", "content": user_msg}],
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=WA_SYSTEM_PROMPT
    )

    return {"answer": answer, "model": "claude-sonnet-4.6"}




if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8086, reload=True)
