from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

BASE_DIR = Path(__file__).resolve().parents[2]
DATABASE_DIR = BASE_DIR / "data" / "database"
DATABASE_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = DATABASE_DIR / "product_factory_v2.sqlite"

engine = create_engine(
    f"sqlite:///{DATABASE_PATH}",
    echo=False,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)

Base = declarative_base()


def init_database():
    from app.models.product import Product
    from app.models.image import ProductImage
    from app.models.specification import ProductSpecification

    Base.metadata.create_all(bind=engine)
    print(f"Database aangemaakt/gecontroleerd: {DATABASE_PATH}")
