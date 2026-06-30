from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "config"
ENV_FILE = CONFIG_DIR / "settings.env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
