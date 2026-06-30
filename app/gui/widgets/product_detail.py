import requests
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QTextBrowser,
    QScrollArea, QFrame, QPushButton, QSizePolicy
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices

from app.services.product_core_service import ProductCoreService
from app.repositories.image_repository import ImageRepository


def format_price(value):
    if value is None or value == "":
        return "-"
    try:
        return f"€ {float(value):.2f}".replace(".", ",")
    except Exception:
        return str(value)


def format_weight(value):
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.3f} kg".replace(".", ",")
    except Exception:
        return str(value)


def attr(record, name, default=""):
    return getattr(record, name, default) or default


class ProductDetail(QWidget):
    def __init__(self):
        super().__init__()
        self.current_sku = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.general = QTextBrowser()
        self.images_area = QScrollArea()
        self.images_area.setWidgetResizable(True)
        self.specs = QTextBrowser()
        self.ai_html = QTextBrowser()
        self.shopify = QTextBrowser()
        self.tabs.addTab(self.general, "Algemeen")
        self.tabs.addTab(self.images_area, "Afbeeldingen")
        self.tabs.addTab(self.specs, "Specificaties")
        self.tabs.addTab(self.ai_html, "AI / HTML")
        self.tabs.addTab(self.shopify, "Shopify")
        layout.addWidget(self.tabs)
        self.show_empty()

    def show_empty(self):
        self.general.setHtml("<h2>Productdetails</h2><p>Klik links op een product.</p>")
        self._set_images_placeholder("Geen product geselecteerd.")
        self.specs.setHtml("<h2>Specificaties</h2><p>Geen product geselecteerd.</p>")
        self.ai_html.setHtml("<h2>AI / HTML</h2><p>Geen product geselecteerd.</p>")
        self.shopify.setHtml("<h2>Shopify</h2><p>Geen product geselecteerd.</p>")

    def load_product(self, sku: str):
        self.current_sku = sku
        record = ProductCoreService.get_product_record(sku)
        if not record:
            self.show_empty()
            return

        title = attr(record, "title") or attr(record, "ai_title") or attr(record, "source_title") or "-"
        ean = attr(record, "ean") or "-"
        self.general.setHtml(f"""
            <h2>{record.sku}</h2>
            <h3>{title}</h3>
            <table cellpadding="6">
                <tr><td><b>EAN</b></td><td>{ean}</td></tr>
                <tr><td><b>Leverancier</b></td><td>{attr(record, 'supplier', '-')}</td></tr>
                <tr><td><b>Merk</b></td><td>{attr(record, 'brand', '-')}</td></tr>
                <tr><td><b>Prijs</b></td><td>{format_price(attr(record, 'price', None))}</td></tr>
                <tr><td><b>Gewicht</b></td><td>{format_weight(attr(record, 'weight', None))}</td></tr>
                <tr><td><b>Producttype</b></td><td>{attr(record, 'product_type', '-')}</td></tr>
                <tr><td><b>Categorie</b></td><td>{attr(record, 'category', '-')}</td></tr>
            </table>
            <hr>
            <h3>Status</h3>
            <p><b>AI gegenereerd:</b> {'Ja' if attr(record, 'ai_generated', False) else 'Nee'}</p>
            <p><b>Shopify geëxporteerd:</b> {'Ja' if attr(record, 'shopify_exported', False) else 'Nee'}</p>
            <p><b>Shopify klaar:</b> {'Ja' if attr(record, 'is_shopify_ready', False) else 'Nee'}</p>
            <p><b>Afbeeldingen:</b> {attr(record, 'image_count', 0)}</p>
            <p><b>Specificaties:</b> {attr(record, 'specification_count', 0)}</p>
        """)
        self._load_images(record.sku)
        self._load_specs(record)
        self._load_ai(record)
        self._load_shopify(record, ean)

    def _set_images_placeholder(self, text: str):
        holder = QWidget()
        layout = QVBoxLayout(holder)
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: #6b7280; padding: 30px;")
        layout.addWidget(label)
        self.images_area.setWidget(holder)

    def _download_pixmap(self, url: str, max_width: int = 420, max_height: int = 320):
        try:
            response = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            pixmap = QPixmap()
            pixmap.loadFromData(response.content)
            if pixmap.isNull():
                return None
            return pixmap.scaled(max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:
            return None

    def _load_images(self, sku: str):
        images = ImageRepository.get_by_sku(sku)
        if not images:
            self._set_images_placeholder("Geen afbeeldingen gevonden.")
            return

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        title = QLabel(f"Afbeeldingen ({len(images)})")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        primary = ImageRepository.get_primary(sku) or images[0]
        main_frame = QFrame()
        main_frame.setObjectName("Card")
        main_layout = QVBoxLayout(main_frame)
        main_label = QLabel()
        main_label.setAlignment(Qt.AlignCenter)
        pixmap = self._download_pixmap(primary.url)
        if pixmap:
            main_label.setPixmap(pixmap)
        else:
            main_label.setText("Afbeelding kon niet geladen worden.")
        main_layout.addWidget(main_label)
        url_label = QLabel(f"<a href='{primary.url}'>{primary.url}</a>")
        url_label.setOpenExternalLinks(True)
        url_label.linkActivated.connect(lambda link: QDesktopServices.openUrl(QUrl(link)))
        url_label.setWordWrap(True)
        main_layout.addWidget(url_label)
        layout.addWidget(main_frame)

        thumbs_layout = QHBoxLayout()
        thumbs_layout.setSpacing(8)
        for image in images[:8]:
            thumb = QLabel()
            thumb.setFixedSize(95, 95)
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setStyleSheet("border: 1px solid #d1d5db; border-radius: 8px; background: #f9fafb;")
            pix = self._download_pixmap(image.url, 85, 85)
            if pix:
                thumb.setPixmap(pix)
            else:
                thumb.setText("-")
            thumbs_layout.addWidget(thumb)
        thumbs_layout.addStretch()
        layout.addLayout(thumbs_layout)

        list_label = QLabel("Alle afbeeldings-URL's:")
        list_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(list_label)
        for image in images:
            link = QLabel(f"{image.position}. <a href='{image.url}'>{image.url}</a>")
            link.setOpenExternalLinks(True)
            link.setWordWrap(True)
            layout.addWidget(link)

        layout.addStretch()
        self.images_area.setWidget(container)

    def _load_specs(self, record):
        if attr(record, "specification_count", 0):
            self.specs.setHtml("<h2>Specificaties</h2><p>Specificaties aanwezig. Detailtabel volgt in de volgende versie.</p>")
        else:
            self.specs.setHtml("<h2>Specificaties</h2><p>Geen specificaties gevonden.</p>")

    def _load_ai(self, record):
        self.ai_html.setHtml(f"""
            <h2>AI / HTML</h2>
            <p><b>AI gegenereerd:</b> {'Ja' if attr(record, 'ai_generated', False) else 'Nee'}</p>
            <h3>Huidige HTML-beschrijving</h3>
            {attr(record, 'html_description') or '<p>-</p>'}
        """)

    def _load_shopify(self, record, ean):
        self.shopify.setHtml(f"""
            <h2>Shopify</h2>
            <table cellpadding="6">
                <tr><td><b>Status</b></td><td>{attr(record, 'shopify_status', 'draft') or 'draft'}</td></tr>
                <tr><td><b>Inventory policy</b></td><td>{attr(record, 'inventory_policy', 'continue') or 'continue'}</td></tr>
                <tr><td><b>Geëxporteerd</b></td><td>{'Ja' if attr(record, 'shopify_exported', False) else 'Nee'}</td></tr>
                <tr><td><b>Klaar voor Shopify</b></td><td>{'Ja' if attr(record, 'is_shopify_ready', False) else 'Nee'}</td></tr>
                <tr><td><b>Prijs</b></td><td>{format_price(attr(record, 'price', None))}</td></tr>
                <tr><td><b>EAN</b></td><td>{ean}</td></tr>
                <tr><td><b>Afbeeldingen</b></td><td>{attr(record, 'image_count', 0)}</td></tr>
            </table>
            <p>Publiceren via Shopify API wordt in een volgende module toegevoegd.</p>
        """)
