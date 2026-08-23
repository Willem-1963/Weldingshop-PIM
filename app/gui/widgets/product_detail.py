from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

import requests
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QTabWidget,
    QTextBrowser,
    QScrollArea,
    QFrame,
    QSizePolicy,
)

from app.services.product_core_service import ProductCoreService
from app.repositories.image_repository import ImageRepository


def safe(value, default: str = "-") -> str:
    if value is None or value == "":
        return default
    return str(value)


def format_price(value) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"€ {float(value):.2f}".replace(".", ",")
    except Exception:
        return str(value)


from app.utils.weight import format_weight


def attr(record, name: str, default=None):
    return getattr(record, name, default)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child_layout is not None:
            clear_layout(child_layout)


def load_pixmap_from_url(url: str, width: int = 360, height: int = 260) -> Optional[QPixmap]:
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        pixmap = QPixmap()
        if not pixmap.loadFromData(response.content):
            return None
        return pixmap.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return None


class InfoRow(QWidget):
    def __init__(self, label: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.label = QLabel(label)
        self.label.setObjectName("Muted")
        self.value = QLabel("-")
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.value.setWordWrap(True)
        self.value.setStyleSheet("font-weight: 600; color: #0f172a;")

        layout.addWidget(self.label)
        layout.addWidget(self.value)

    def set_value(self, value):
        self.value.setText(safe(value))


class ProductDetail(QWidget):
    def __init__(self):
        super().__init__()
        self.current_sku: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.header = QLabel("Productdetails")
        self.header.setStyleSheet("font-size: 20px; font-weight: 700; color: #0f172a;")
        self.header.setWordWrap(True)
        root.addWidget(self.header)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.general_tab = QWidget()
        self.images_tab = QWidget()
        self.specs_tab = QWidget()
        self.ai_tab = QWidget()
        self.shopify_tab = QWidget()

        self.tabs.addTab(self.general_tab, "Algemeen")
        self.tabs.addTab(self.images_tab, "Afbeeldingen")
        self.tabs.addTab(self.specs_tab, "Specificaties")
        self.tabs.addTab(self.ai_tab, "AI / HTML")
        self.tabs.addTab(self.shopify_tab, "Shopify")

        self._build_general_tab()
        self._build_images_tab()
        self._build_specs_tab()
        self._build_ai_tab()
        self._build_shopify_tab()
        self.show_empty()

    def _build_general_tab(self):
        layout = QGridLayout(self.general_tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(28)
        layout.setVerticalSpacing(14)

        self.general_fields = {
            "sku": InfoRow("SKU"),
            "title": InfoRow("Titel"),
            "ean": InfoRow("EAN"),
            "supplier": InfoRow("Leverancier"),
            "brand": InfoRow("Merk"),
            "price": InfoRow("Prijs"),
            "weight": InfoRow("Gewicht"),
            "product_type": InfoRow("Producttype"),
            "category": InfoRow("Categorie"),
            "images": InfoRow("Afbeeldingen"),
            "specs": InfoRow("Specificaties"),
            "shopify_ready": InfoRow("Shopify klaar"),
        }

        positions = [
            ("sku", 0, 0), ("title", 0, 1),
            ("ean", 1, 0), ("supplier", 1, 1),
            ("brand", 2, 0), ("price", 2, 1),
            ("weight", 3, 0), ("product_type", 3, 1),
            ("category", 4, 0), ("images", 4, 1),
            ("specs", 5, 0), ("shopify_ready", 5, 1),
        ]
        for key, row, col in positions:
            layout.addWidget(self.general_fields[key], row, col)

        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(6, 1)

    def _build_images_tab(self):
        layout = QVBoxLayout(self.images_tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.image_count_label = QLabel("Afbeeldingen")
        self.image_count_label.setStyleSheet("font-size: 17px; font-weight: 700;")
        layout.addWidget(self.image_count_label)

        self.image_preview = QLabel("Geen afbeelding geselecteerd")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setMinimumHeight(280)
        self.image_preview.setStyleSheet(
            "background: white; border: 1px solid #d1d5db; border-radius: 8px; color: #64748b;"
        )
        layout.addWidget(self.image_preview)

        self.image_url = QTextBrowser()
        self.image_url.setMaximumHeight(90)
        self.image_url.setOpenExternalLinks(True)
        layout.addWidget(self.image_url)

        self.thumbnail_area = QScrollArea()
        self.thumbnail_area.setWidgetResizable(True)
        self.thumbnail_area.setMaximumHeight(150)
        self.thumbnail_container = QWidget()
        self.thumbnail_layout = QHBoxLayout(self.thumbnail_container)
        self.thumbnail_layout.setContentsMargins(0, 0, 0, 0)
        self.thumbnail_layout.setSpacing(8)
        self.thumbnail_area.setWidget(self.thumbnail_container)
        layout.addWidget(self.thumbnail_area)

    def _build_specs_tab(self):
        layout = QVBoxLayout(self.specs_tab)
        layout.setContentsMargins(12, 12, 12, 12)
        self.specs_browser = QTextBrowser()
        layout.addWidget(self.specs_browser)

    def _build_ai_tab(self):
        layout = QVBoxLayout(self.ai_tab)
        layout.setContentsMargins(12, 12, 12, 12)
        self.ai_browser = QTextBrowser()
        layout.addWidget(self.ai_browser)

    def _build_shopify_tab(self):
        layout = QVBoxLayout(self.shopify_tab)
        layout.setContentsMargins(12, 12, 12, 12)
        self.shopify_browser = QTextBrowser()
        layout.addWidget(self.shopify_browser)

    def show_empty(self):
        self.header.setText("Productdetails")
        for field in getattr(self, "general_fields", {}).values():
            field.set_value("-")
        self.image_count_label.setText("Afbeeldingen")
        self.image_preview.setText("Klik links op een product")
        self.image_preview.setPixmap(QPixmap())
        self.image_url.setHtml("<p>Geen product geselecteerd.</p>")
        clear_layout(self.thumbnail_layout)
        self.specs_browser.setHtml("<h2>Specificaties</h2><p>Geen product geselecteerd.</p>")
        self.ai_browser.setHtml("<h2>AI / HTML</h2><p>Geen product geselecteerd.</p>")
        self.shopify_browser.setHtml("<h2>Shopify</h2><p>Geen product geselecteerd.</p>")

    def load_product(self, sku: str):
        self.current_sku = sku
        record = ProductCoreService.get_product_record(sku)
        if not record:
            self.show_empty()
            return

        title = attr(record, "title") or attr(record, "ai_title") or attr(record, "source_title") or "-"
        self.header.setText(f"{record.sku} — {title}")

        self.general_fields["sku"].set_value(record.sku)
        self.general_fields["title"].set_value(title)
        self.general_fields["ean"].set_value(attr(record, "ean") or "-")
        self.general_fields["supplier"].set_value(attr(record, "supplier") or "-")
        self.general_fields["brand"].set_value(attr(record, "brand") or "-")
        self.general_fields["price"].set_value(format_price(attr(record, "price")))
        self.general_fields["weight"].set_value(format_weight(attr(record, "weight")))
        self.general_fields["product_type"].set_value(attr(record, "product_type") or "-")
        self.general_fields["category"].set_value(attr(record, "category") or "-")
        self.general_fields["images"].set_value(attr(record, "image_count", 0))
        self.general_fields["specs"].set_value(attr(record, "specification_count", 0))
        self.general_fields["shopify_ready"].set_value("Ja" if attr(record, "is_shopify_ready", False) else "Nee")

        self._load_images(record)
        self._load_specs(record)
        self._load_ai(record)
        self._load_shopify(record)

    def _load_images(self, record):
        clear_layout(self.thumbnail_layout)
        images = ImageRepository.get_by_sku(record.sku)
        self.image_count_label.setText(f"Afbeeldingen ({len(images)})")

        if not images:
            self.image_preview.setPixmap(QPixmap())
            self.image_preview.setText("Geen afbeeldingen gevonden")
            self.image_url.setHtml("<p>Geen afbeeldingen gekoppeld.</p>")
            return

        self._show_image(images[0].url)
        links = [f"<li><a href='{html.escape(img.url)}'>{html.escape(img.url)}</a></li>" for img in images]
        self.image_url.setHtml("<b>Afbeelding-URL's</b><ol>" + "".join(links) + "</ol>")

        for img in images:
            thumb = QLabel()
            thumb.setFixedSize(92, 92)
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setStyleSheet("background:white; border:1px solid #d1d5db; border-radius:6px;")
            pixmap = load_pixmap_from_url(img.url, 86, 86)
            if pixmap:
                thumb.setPixmap(pixmap)
            else:
                thumb.setText("geen\npreview")
            thumb.mousePressEvent = lambda event, url=img.url: self._show_image(url)
            self.thumbnail_layout.addWidget(thumb)
        self.thumbnail_layout.addStretch(1)

    def _show_image(self, url: str):
        pixmap = load_pixmap_from_url(url, 520, 320)
        if pixmap:
            self.image_preview.setText("")
            self.image_preview.setPixmap(pixmap)
        else:
            self.image_preview.setPixmap(QPixmap())
            self.image_preview.setText("Afbeelding kan niet worden geladen")

    def _load_specs(self, record):
        if attr(record, "specification_count", 0):
            self.specs_browser.setHtml("<h2>Specificaties</h2><p>Specificaties aanwezig. Detailtabel volgt in een volgende versie.</p>")
        else:
            self.specs_browser.setHtml("<h2>Specificaties</h2><p>Geen specificaties gevonden.</p>")

    def _load_ai(self, record):
        title = attr(record, "title") or attr(record, "ai_title") or attr(record, "source_title") or "-"
        description = attr(record, "html_description") or "<p>-</p>"
        self.ai_browser.setHtml(f"""
            <h2>AI / HTML</h2>
            <p><b>AI gegenereerd:</b> {'Ja' if attr(record, 'ai_generated', False) else 'Nee'}</p>
            <p><b>Titel:</b> {html.escape(str(title))}</p>
            <h3>HTML beschrijving</h3>
            {description}
        """)

    def _load_shopify(self, record):
        self.shopify_browser.setHtml(f"""
            <h2>Shopify</h2>
            <table cellpadding="6">
                <tr><td><b>Status</b></td><td>{html.escape(str(attr(record, 'shopify_status') or 'draft'))}</td></tr>
                <tr><td><b>Inventory policy</b></td><td>{html.escape(str(attr(record, 'inventory_policy') or 'continue'))}</td></tr>
                <tr><td><b>Geëxporteerd</b></td><td>{'Ja' if attr(record, 'shopify_exported', False) else 'Nee'}</td></tr>
                <tr><td><b>Klaar voor Shopify</b></td><td>{'Ja' if attr(record, 'is_shopify_ready', False) else 'Nee'}</td></tr>
                <tr><td><b>Prijs</b></td><td>{format_price(attr(record, 'price'))}</td></tr>
                <tr><td><b>EAN</b></td><td>{html.escape(str(attr(record, 'ean') or '-'))}</td></tr>
                <tr><td><b>Afbeeldingen</b></td><td>{attr(record, 'image_count', 0)}</td></tr>
            </table>
            <p>Publiceren via Shopify API wordt in een volgende module toegevoegd.</p>
        """)
