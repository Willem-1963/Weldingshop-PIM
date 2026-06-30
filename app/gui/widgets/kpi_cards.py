from PySide6.QtWidgets import QFrame, QLabel, QHBoxLayout, QVBoxLayout
from app.core.database import SessionLocal
from app.models.product import Product
from app.models.image import ProductImage


class KpiCard(QFrame):
    def __init__(self, title: str, value: str, subtitle: str = ""):
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        title_label = QLabel(title)
        title_label.setObjectName("KpiTitle")
        value_label = QLabel(value)
        value_label.setObjectName("KpiValue")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("Muted")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(subtitle_label)


class KpiCards(QFrame):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        for card in self._build_cards():
            layout.addWidget(card)

    def _build_cards(self):
        session = SessionLocal()
        try:
            products = session.query(Product).count()
            images = session.query(ProductImage).count()
            ai_done = session.query(Product).filter(Product.ai_generated.is_(True)).count()
            with_images = session.query(ProductImage.sku).distinct().count()
        finally:
            session.close()
        return [
            KpiCard("Producten", str(products), "totaal in database"),
            KpiCard("AI gereed", str(ai_done), "producten met AI-status"),
            KpiCard("Met afbeelding", str(with_images), "producten met minimaal 1 afbeelding"),
            KpiCard("Afbeeldingen", str(images), "totaal gekoppeld"),
        ]
