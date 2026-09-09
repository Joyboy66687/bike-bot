"""Configuration shared by the bot and background services."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATABASE_PATH = BASE_DIR / "debts.db"
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
CLEAR_DB_PIN = os.getenv("CLEAR_DB_PIN", "7777")
try:
    ADMIN_IDS = [int(v.strip()) for v in os.getenv("ADMIN_IDS", "").split(",") if v.strip()]
except ValueError as exc:
    raise RuntimeError("ADMIN_IDS должен содержать Telegram ID через запятую") from exc
TZ = ZoneInfo(os.getenv("TZ", "Europe/Warsaw"))
BACKUP_PASSPHRASE_PATH = BASE_DIR / ".backup_passphrase"
