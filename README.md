# QineSystems Telegram Bot & API

A Telegram learning bot and FastAPI service built for **QineSystems** (`@QineSystems` / `@QineSystems_bot`), supporting curriculum navigation, study resources, quizzes, flashcards, AI assistance, subscription gating, and an admin management panel.

## Tech Stack

- **Bot & API Framework:** Python, `aiogram` 3.x, FastAPI, Uvicorn
- **ORM & Database:** SQLAlchemy (async), `asyncpg` (PostgreSQL), `aiosqlite` (local SQLite fallback)
- **AI Integration:** Google Gemini (`GEMINI_API_KEY`)

## Project Structure

- `main.py` — Initializes the database, registers routers, and runs the FastAPI server (`0.0.0.0:$PORT`) alongside `aiogram` polling with retry handling.
- `database.py` — Configures the async SQLAlchemy engine, `AsyncSessionLocal`, `Base`, and `get_db()`, normalizing `postgres://` / `postgresql://` URLs and handling SSL/SQLite connection options.
- `bot.py` — Core student flows, subscription gating, empty-state "Coming Soon" responses, and resource delivery (PDFs/documents, photos, and direct text notices).
- `student_resources.py` — Student resource browsing flows (including COC, study notes, worksheets, and reference materials).
- `admin_crud.py` — Admin CRUD panel for courses, chapters, resources (PDF/photo/text notices, free-sample overrides, type moves, publish toggles, renames, and 2-step delete confirmations), quizzes, flashcards, and students.
- `admin_dashboard_pro.py` & `admin_handlers.py` — Admin control center and management handlers.
- `bulk_handlers.py`, `ai_handlers.py`, `tools_handlers.py`, `services.py`, `api.py` — Bulk operations, AI tutoring/assistance, study tools, initial seeding, and FastAPI routes.

## Environment Variables

Create a local `.env` file (excluded from Git via `.gitignore`) and configure the same variables in your hosting environment:

```env
BOT_TOKEN=your_telegram_bot_token
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DBNAME
GEMINI_API_KEY=your_gemini_api_key
ADMIN_TELEGRAM_IDS=123456789
AI_MODEL_NAME=gemini-2.5-flash
PORT=8000
