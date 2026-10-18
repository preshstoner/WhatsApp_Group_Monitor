# WhatsApp Group Monitor & AI Summary System

A production-grade, fully automated WhatsApp monitoring, indexing, and AI-powered reporting system. Built with **FastAPI**, **PostgreSQL**, **Evolution API (Baileys)**, and **Resend**.

---

## 🚀 Key Features

* **Real-Time Webhook Ingestion:** Automatically captures messages, group updates, and participant events via Evolution API using asynchronous background processing.
* **Connection Watchdog:** A resilient background loop running every 60 seconds that monitors the WhatsApp connection state, auto-reconnects on drops, and recreates missing instances automatically.
* **PostgreSQL Partitioning & Maintenance:** Handles heavy message volumes cleanly using monthly table partitioning with automated look-ahead creation and 30-day log retention cleanup.
* **AI-Powered Summaries & Search:** Leverages full-text search (GIN index) for sub-100ms message queries across groups, alongside automated AI summaries.
* **Automated Email Reports:** Scheduled email dispatching via Resend for daily group intelligence digests.
* **Prometheus & Health Metrics:** Built-in health check endpoints and Prometheus metrics endpoint (`/metrics`) for comprehensive monitoring.

---

## 🛠️ Technology Stack

* **Backend:** Python 3.11+, FastAPI, Uvicorn, Asyncpg, Httpx
* **Database:** PostgreSQL (with automated partitioning and GIN indexing)
* **WhatsApp Gateway:** Evolution API (WHATSAPP-BAILEYS integration)
* **Email Delivery:** Resend API / SMTP
* **Containerization:** Docker & Docker Compose

---

⚙️ Quick Start Checklist
Clone the Repository & Setup Environment:
Ensure you have Docker Desktop and Python 3.11+ installed. Create your environment configuration file (.env) with your passwords and API keys.
Launch Infrastructure:
Bash
docker compose up -d
Initialize the Database:
Run the SQL schema script inside your PostgreSQL container or via DBeaver to create the required tables and indexes.
Install Python Dependencies:
Bash
pip install -r requirements.txt
Run the Application:
Bash
python main.py
Link WhatsApp:
Access your Evolution API instance or use the provided QR code endpoints to link your dedicated monitoring phone number.


## 📂 Project Structure

```text
WhatsApp_Group_Monitor/
├── main.py                  # Core FastAPI application (webhooks, watchdog, endpoints)
├── init_schema.sql          # PostgreSQL schema and partition management
├── Dockerfile               # Container build configuration for FastAPI
├── docker-compose.yml       # Stack configuration (PostgreSQL, Evolution API, Redis)
├── prometheus.yml           # Prometheus metrics scrape configuration
├── qrcode.html              # Live QR code linking interface
├── requirements.txt         # Python dependencies
└── web-server/              # PHP dashboard interface proxy files
    ├── whatsapp.php         # Group monitor UI
    ├── whatsapp-data.php    # AJAX proxy
    └── email-reports.php    # Email report management UI


