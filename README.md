# haiwaike - AI-Powered B2B Lead Generation Platform

<p align="center">
  <strong>An end-to-end intelligent outbound prospecting system that searches, qualifies, personalizes, and sends emails to B2B leads on autopilot.</strong>
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

AutoLeadGen is a **full-stack lead generation platform** built for outbound sales teams, SDRs, and solo founders who want to automate their B2B prospecting workflow. Instead of manually searching Google, looking up emails on Snov.io, writing personalized emails one by one, and sending them through Gmail, this system does all of it in a continuous background loop.

**The typical flow:**

```
Define your ideal customer → AI searches the web → Finds decision-makers → 
Verifies emails → Writes personalized outreach → Sends via SMTP → 
Monitors inbox for replies → AI drafts follow-ups
```

It's like having a 24/7 SDR that never sleeps, never gets tired, and gets smarter over time through your feedback.

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
- **Multi-account rotation** - Round-robin across multiple SMTP accounts
- **Daily sending caps** - Configurable per-account limits to stay under radar
- **Cooldown periods** - Domain-level rate limiting to avoid spam filters
- **HTML email templates** - Professional branded emails with proper formatting
- **Thread tracking** - Follow-ups maintain proper In-Reply-To headers

### Reply Handling
- **IMAP inbox monitoring** - Automatically detects when leads reply
- **Intent analysis** - AI classifies replies as interested, not interested, unsubscribe, etc.
- **AI draft generation** - One-click generate personalized follow-up responses
- **Editable drafts** - Modify AI-generated drafts before sending
- **One-click send** - Send directly from the dashboard

### Multi-Channel Outreach
- **LinkedIn** - Send connection requests with personalized notes via Unipile
- **WhatsApp** - Send messages to leads via Unipile
- **Email** - Full SMTP/IMAP integration

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
│  │              Background Worker Threads                 │  │
│  │  • Prospecting loop (search → enrich → draft)         │  │
│  │  • Outbound loop (send emails with rate limiting)     │  │
│  │  • Inbox monitor (IMAP polling for replies)           │  │
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
| **Backend** | FastAPI, SQLAlchemy, Uvicorn, Python 3.9+ |
| **Database** | MySQL 8.0 / MariaDB |
| **AI/LLM** | Alibaba Bailian (DeepSeek V4 Pro), OpenAI-compatible API |
| **Search** | Tavily AI Search, Bocha AI Search |
| **Enrichment** | Snov.io (email finder + verification) |
| **Messaging** | Unipile (LinkedIn + WhatsApp API) |
| **Email** | SMTP/IMAP via Python `smtplib` / `imaplib` |
| **Auth** | JWT (python-jose) + bcrypt password hashing |
| **Deployment** | Docker, Docker Compose |

---

## Quick Start

### Prerequisites
- Python 3.9+
- Node.js 18+
- MySQL 8.0+ (or use the included Docker Compose)
- API keys: [Snov.io](https://snov.io), [Tavily](https://tavily.com), [Alibaba Bailian](https://bailian.console.aliyun.com)

### Option 1: Docker Compose (Recommended)

```bash
git clone https://github.com/lhl-bot/AutoLeadGen.git
cd AutoLeadGen
cp .env.example .env
# Edit .env with your API keys and database credentials

docker compose up -d --build
```

Access the app at `http://localhost:3000`

### Option 2: Local Development

**Backend:**

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Run database migrations
python migrate.py
python migrate_v2.py
python migrate_v3.py
python migrate_v4.py
python migrate_v5.py
python migrate_v6.py
python migrate_v7.py

# Start backend
uvicorn main:app --reload --port 8001
```

**Backend tests:**

```bash
pip install -r requirements-dev.txt
pytest
```

**Frontend:**

```bash
cd frontend

# Install dependencies
npm install

# Configure frontend
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8001" > .env.local

# Start frontend
npm run dev
```

Access the app at `http://localhost:3000`

---

## Configuration

The `.env` file is the single source of truth for all configuration. Key settings:

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | MySQL connection string | Yes |
| `JWT_SECRET_KEY` | Secret for signing JWT tokens | Yes |
| `LLM_API_KEY` | Alibaba Bailian API key | Yes |
| `LLM_BASE_URL` | LLM API endpoint | Yes |
| `LLM_MODEL` | Model name (e.g., `deepseek-v4-pro`) | Yes |
| `SNOVIO_CLIENT_ID` | Snov.io client ID | Yes |
| `SNOVIO_CLIENT_SECRET` | Snov.io client secret | Yes |
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
├── main.py                    # FastAPI app entry point + lifespan
├── models.py                  # SQLAlchemy ORM models
├── schemas.py                 # Pydantic request/response schemas
├── database.py                # Database engine + session factory
├── requirements.txt           # Python dependencies
│
├── routers/                   # API route handlers
│   ├── auth.py               # Login / JWT token
│   ├── workflows.py          # Outreach workflow CRUD
│   ├── leads.py              # Lead management + rating
│   ├── replies.py            # Reply tracking + AI draft generation
│   ├── personas.py           # Customer persona management
│   ├── channels.py           # LinkedIn/WhatsApp channels
│   └── ...
│
├── services/                  # Business logic
│   ├── outbound_engine.py    # Core outreach loop (search → draft → send)
│   ├── search_engine.py      # Web search orchestration
│   ├── ai_writer.py          # LLM email generation
│   ├── agent_core.py         # AI conversational agent
│   ├── lead_scoring.py       # AI-powered lead fit scoring
│   ├── followup_engine.py    # Reply intent analysis + draft generation
│   ├── snovio_client.py      # Snov.io API client
│   ├── unipile_client.py     # Unipile (LinkedIn/WhatsApp) client
│   ├── inbox_monitor.py      # IMAP polling for replies/bounces
│   ├── email_sender.py       # SMTP email sending
│   ├── email_verifier.py     # DNS/MX email validation
│   ├── preference_learner.py # RLHF feedback loop
│   └── ...
│
├── frontend/                  # Next.js 16 frontend
│   ├── src/app/
│   │   ├── dashboard/        # Dashboard pages
│   │   │   ├── page.tsx      # Analytics overview
│   │   │   ├── workflows/    # Workflow management
│   │   │   ├── replies/      # Reply inbox + AI drafts
│   │   │   ├── personas/     # Customer personas
│   │   │   ├── pools/        # Client pools
│   │   │   ├── agent/        # AI chat assistant
│   │   │   └── ...
│   │   ├── login/            # Login page
│   │   └── layout.tsx        # Root layout
│   └── src/components/       # Reusable UI components
│
├── Dockerfile                 # Backend Docker image
├── docker-compose.yml         # Full stack orchestration
├── migrate*.py                # Database migration scripts
└── .env.example               # Environment template
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

For production deployment on a cloud server:

1. Set up a Linux server (Alibaba Cloud ECS, AWS EC2, etc.)
2. Install Docker and Docker Compose
3. Set up a MySQL database (Alibaba Cloud RDS recommended)
4. Clone the repo and configure `.env`
5. Run `docker compose up -d --build`

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed instructions.

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
