# haiwaike - AI-Powered B2B Lead Generation Platform

<p align="center">
  <strong>A Company-first, human-governed multi-channel sales system built around qualified opportunities.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/backend-FastAPI-009688?style=flat-square&logo=fastapi" />
  <img src="https://img.shields.io/badge/frontend-Next.js%2016-black?style=flat-square&logo=next.js" />
  <img src="https://img.shields.io/badge/database-MySQL-blue?style=flat-square&logo=mysql" />
  <img src="https://img.shields.io/badge/AI-DeepSeek%20V4-6366f1?style=flat-square" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" />
</p>

---

## What is this?

> **Product V2 release scope:** the active product is Company-first and
> Opportunity-led. FastAPI does not create tables or start worker threads at boot;
> Alembic owns schema changes and database-leased processes own automation. Local
> execution is fake-only. The checked-in production path supports reviewed Email
> plus separately approved, exact-owner automatic Email over SMTP, and
> reply/bounce/complaint ingestion over IMAP; LinkedIn, WhatsApp,
> prospecting, and research have no approved real V2 connector in this release.
> The invitation pilot includes a five-step first-contact activation flow and a
> shared CSV/AI candidate workspace, while paid acquisition remains fake-only
> until its independent safety and cost gate is approved.

AutoLeadGen is a full-stack sales-development platform for managing Companies,
Contacts, contact points, Campaign revisions, multi-Campaign Enrollments,
Conversations, human-confirmed Opportunities, Tasks, consent, cost, and audit history.
Automation is subordinate to persisted readiness and hard safety gates.

**The typical flow:**

```
Publish ICP and Campaign revision → Build Companies, Contacts, and Lists →
Pass persisted readiness checks → Enroll contacts → Execute channel steps →
Classify replies → Human confirms handoff → Manage qualified Opportunity
```

AI proposes research, classifications, and changes; people publish ICP changes,
confirm positive signals, and own commercial decisions. Consent, invalid contact
points, duplicate prevention, safety locks, account faults, and capacity limits are
never bypassed by an AI or manual soft override.

---

## Key Features

### Search & Discovery
- **AI-powered web search** - Uses Tavily and Bocha AI to find companies matching your ideal customer profile
- **Multi-region search** - Automatically targets leads across different countries and languages
- **Keyword mutation** - When one search vector is exhausted, AI generates new keywords to keep exploring
- **Domain deduplication** - Never contacts the same company twice
- **Buyer intent signals** - Prioritizes companies showing buying behavior online

### Email Enrichment & Verification
- **Snov.io integration** - Finds verified email addresses for decision-makers at target companies
- **Role-based filtering** - Targets specific positions (CEO, Founder, Buyer, etc.)
- **DNS/MX validation** - Verifies email deliverability before sending
- **Bounce rate monitoring** - Auto-pauses sending if bounce rate exceeds threshold

### AI Email Writing
- **DeepSeek V4 Pro powered** - Uses Alibaba Bailian (百炼) API for high-quality email generation
- **Context-aware personalization** - Each email references the prospect's company, products, recent news
- **RLHF feedback loop** - Your positive/negative ratings teach the AI what works
- **Multi-language support** - Writes in English, Chinese, Spanish, German, and more

### Automated Outreach
- **Immutable reviewed messages** - The exact subject/body snapshot is approved before SMTP
- **Bound sender identity** - Every attempt freezes its owner, channel, and sender account
- **Daily sending caps** - Per-account and campaign budget enforcement at the final boundary
- **Cooldown and consent gates** - Contact/company locks prevent unsafe cross-campaign sends
- **Uncertain-delivery containment** - Ambiguous SMTP outcomes are never auto-retried
- **Complaint containment** - Abuse reports suppress the recipient and hard-lock the sender account
- **Controlled automatic mode** - Exact owner allowlist, approval ID, and deployment/account caps are rechecked at SMTP

### Reply Handling
- **IMAP inbox monitoring** - Automatically detects when leads reply
- **Intent analysis** - AI classifies replies as interested, not interested, unsubscribe, etc.
- **AI draft generation** - One-click generate personalized follow-up responses
- **Editable drafts** - Modify AI-generated drafts before sending
- **One-click send** - Send directly from the dashboard

### Multi-Channel Outreach
- **Email** - Production connector through SMTP/IMAP with Review-first and separately approved Auto modes
- **LinkedIn / WhatsApp** - Modeled in V2 but deliberately blocked until separate connectors and approvals exist

### Dashboard & Analytics
- **Dark-themed UI** - Clean, modern dashboard built with Next.js 16 + Tailwind CSS + shadcn/ui
- **Real-time analytics** - Track leads found, emails sent, replies received, bounce rates
- **Workflow management** - Create and manage multiple outreach campaigns
- **Customer personas** - Define ideal customer profiles with detailed targeting criteria
- **Client pools** - Organize leads into pools for better management
- **Multi-user support** - Admin and regular user roles with data isolation
- **Bilingual** - Full Chinese/English interface switching

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend (Next.js 16)                     │
│              localhost:3000 · React 19 · Tailwind            │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP API
┌──────────────────────▼──────────────────────────────────────┐
│                   Backend (FastAPI)                           │
│              localhost:8001 · Python · SQLAlchemy            │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Search   │  │ Email    │  │ Inbox    │  │ AI Agent │   │
│  │ Engine   │  │ Sender   │  │ Monitor  │  │ Core     │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │              │              │              │         │
│  ┌────▼──────────────▼──────────────▼──────────────▼─────┐  │
│  │          Dedicated database-leased V2 workers          │  │
│  │  • local/test: deterministic fake queue consumers       │  │
│  │  • production: gated SMTP outbound + IMAP safety inbox  │  │
│  │  • MySQL job/outbox · persisted cursor · lease fencing  │  │
│  └───────────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐  ┌──────────┐  ┌──────────┐
   │ MySQL   │  │ External │  │  LLM API │
   │Database │  │   APIs   │  │(DeepSeek)│
   └─────────┘  └──────────┘  └──────────┘
                   │
          ┌────────┼────────┬──────────┐
          ▼        ▼        ▼          ▼
       Snov.io  Tavily   Bocha    Unipile
      (enrich) (search) (search) (LinkedIn/
                                 WhatsApp)
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | Next.js 16 (Turbopack), React 19, TypeScript, Tailwind CSS, shadcn/ui, Recharts, Framer Motion |
| **Backend** | FastAPI, SQLAlchemy, Gunicorn/Uvicorn Worker, Python 3.11 |
| **Database** | MySQL 8.0 / MariaDB |
| **AI/LLM** | Alibaba Bailian (DeepSeek V4 Pro), OpenAI-compatible API |
| **Search** | Tavily AI Search, Bocha AI Search |
| **Enrichment** | Snov.io (email finder + verification) |
| **Messaging** | Unipile (LinkedIn + WhatsApp API) |
| **Email** | SMTP/IMAP via Python `smtplib` / `imaplib` |
| **Auth** | JWT (PyJWT) + Argon2 hashing with legacy bcrypt verification |
| **Deployment** | Docker, Docker Compose |

---

## Quick Start

### Prerequisites

- Python 3.11 or newer
- Node.js 22
- `openssl` and `curl`

Docker and external API keys are not required for the safe local workspace.
Local development uses an isolated SQLite database, fake connectors, and a hard
outbound pause by default.

### Recommended local workspace

```bash
git clone https://github.com/lhl-bot/AutoLeadGen.git
cd AutoLeadGen
./scripts/dev.sh setup
./run.sh
```

Open `http://localhost:3000`. The local username is `acceptance-admin`; run
`./scripts/dev.sh password` in another terminal to read the generated password.
Press Ctrl-C in the first terminal to stop both services.

Useful commands:

```bash
./scripts/dev.sh status  # Probe frontend, API, and database readiness
./scripts/dev.sh check   # Run backend tests and all frontend checks
```

Local state and logs live under `.local/dev/` and are ignored by Git. To avoid
port conflicts, override `AUTOLEADGEN_BACKEND_PORT` or
`AUTOLEADGEN_FRONTEND_PORT` when starting the app.

### Manual development

**Backend:**

```bash
# Create the canonical virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements-dev.txt

# Configure an isolated database and fake-only runtime, or use ./run.sh

# Run the additive Product V2 migration chain
python -m alembic upgrade head

# Start backend
uvicorn main:app --reload --port 8001
```

**Frontend:**

```bash
cd frontend

# Install dependencies
npm ci

# Configure frontend
echo "BACKEND_URL=http://127.0.0.1:8001" > .env.local

# Start frontend
npm run dev
```

Access the app at `http://localhost:3000`

---

## Configuration

Process environment variables override `.env`; production uses mounted secret
files instead of committed values. Key settings:

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | MySQL connection string | Yes |
| `JWT_SECRET_KEY` | Secret for signing JWT tokens | Yes |
| `LLM_API_KEY` | Alibaba Bailian API key | Yes |
| `LLM_BASE_URL` | LLM API endpoint | Yes |
| `LLM_MODEL` | Model name (e.g., `deepseek-v4-pro`) | Yes |
| `SNOVIO_ENABLED` | Enable optional Snov.io enrichment; defaults to `false` | Optional |
| `SNOVIO_CLIENT_ID` | Snov.io client ID | Only when Snov.io is enabled |
| `SNOVIO_CLIENT_SECRET` | Snov.io client secret | Only when Snov.io is enabled |
| `TAVILY_API_KEY` | Tavily search API key | Recommended |
| `BOCHA_API_KEY` | Bocha search API key | Optional |
| `UNIPILE_API_KEY` | Unipile API key for LinkedIn/WhatsApp | Optional |
| `ENABLE_BACKGROUND_WORKERS` | Enable auto-outreach loops | Optional |
| `EMAIL_MAX_DAILY_PER_ACCOUNT` | Daily email cap per SMTP account | Optional |
| `PUBLIC_APP_URL` | Public base URL used for one-click unsubscribe links | Production |
| `UNIPILE_WEBHOOK_SECRET` | HMAC secret used to verify Unipile webhooks | Production if Unipile is enabled |
| `OUTBOUND_AUTO_SEND_DRAFTS` | Auto-send AI drafts in background workers. Defaults to `false` for review-first pilots | Optional |
| `CREDITS_ENABLED` | Enable per-user credit checks and debit ledger | Optional |
| `CREDITS_DEFAULT_BALANCE` | Trial credits for new and migrated users | Optional |
| `CREDITS_COST_*` | Per-action credit pricing for drafts, email, LinkedIn, and WhatsApp | Optional |

See `.env.example` for the full list with descriptions.

---

## Project Structure

```
AutoLeadGen/
├── main.py                    # FastAPI API process (no workers or DDL at boot)
├── database.py                # Database engine + session factory
├── models.py / schemas.py     # Legacy compatibility model and API contract
├── alembic/                   # The only active schema migration chain
├── product_v2/                # Company-first domain, API, runtime, and workers
│
├── routers/                   # Legacy/read-compatible API surface
├── services/                  # Shared and legacy application services
│
├── frontend/                  # Next.js 16 frontend
│   ├── src/app/               # Routes and route composition
│   ├── src/features/v2/       # Product V2 API, pages, and components
│   └── src/components/        # Shared and legacy UI components
│
├── scripts/                   # Local, migration, release, and operations tools
│   ├── dev.sh                 # Canonical safe local entrypoint
│   └── product_v2_local.sh    # Optional isolated MySQL 8 integration environment
├── tests/                     # Backend unit/integration contract tests
├── docs/product_v2/           # V2 development and production runbooks
├── compose.product-v2.yml     # Optional local MySQL only
├── compose.production.yml     # Reviewed production topology
└── migrate*.py                # Historical legacy migrations; do not run for V2
```

---

## API Endpoints

All API routes are under `/api/` and require JWT authentication (except `/api/auth/login`).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/login` | Authenticate and get JWT token |
| GET | `/api/analytics/dashboard` | Dashboard statistics |
| GET/POST | `/api/workflows/` | List/create outreach workflows |
| GET/POST | `/api/personas/` | List/create customer personas |
| GET/POST | `/api/email_accounts/` | List/create SMTP accounts |
| GET | `/api/replies/` | List leads that have replied |
| POST | `/api/replies/{id}/generate-draft` | Generate AI reply draft |
| POST | `/api/replies/{id}/send` | Send edited reply to lead |
| PUT | `/api/leads/{id}` | Update lead details |
| POST | `/api/leads/{id}/rate` | Rate lead (positive/negative) for RLHF |
| POST | `/api/leads/{id}/score` | Recalculate AI fit score |
| GET | `/api/channels/` | List LinkedIn/WhatsApp channels |

---

## Screenshots

<details>
<summary>Click to expand</summary>

The dashboard includes:

- **Analytics Overview** - Real-time stats on leads found, emails sent, reply rates
- **Workflow Management** - Configure search criteria, email templates, sending schedules
- **Reply Inbox** - View all replies, generate AI drafts, edit and send
- **AI Agent** - Chat with the AI assistant to search leads, analyze data
- **Customer Personas** - Define ideal customer profiles
- **Email Logs** - Full audit trail of all sent emails

</details>

---

## Deployment

Do not use the development `docker-compose.yml` for production. Production uses
digest-pinned signed images, mounted secret files, TLS, migration/preflight jobs,
live control files, and the email-only canary sequence in
[`docs/product_v2/PRODUCTION_CUTOVER_RUNBOOK.md`](docs/product_v2/PRODUCTION_CUTOVER_RUNBOOK.md).
The operator starts from `deploy/production.env.example` and
`compose.production.yml`; real SMTP remains hard-paused until the enable-real
preflight and external approvals pass.

---

## Roadmap

- [ ] Email A/B testing
- [ ] CRM integration (Salesforce, HubSpot)
- [ ] Webhook support for real-time notifications
- [ ] Multi-tenant SaaS mode
- [ ] Advanced analytics with funnel visualization
- [ ] API rate limiting middleware
- [ ] Unified LLM client abstraction

---

## Contributing

This is an open-source project. Issues, PRs, and feature requests are welcome.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <strong>Built for outbound teams who want to scale without scaling headcount.</strong><br/>
  <sub>Made with FastAPI + Next.js + DeepSeek AI</sub>
</p>
