from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "input"
OUTPUT_DIR = DATA_DIR / "output"
DATABASE_DIR = DATA_DIR / "database"

RAW_DATABASE_PATH = DATABASE_DIR / "product_factory.sqlite"
ORM_DATABASE_PATH = DATABASE_DIR / "product_factory_v2.sqlite"
