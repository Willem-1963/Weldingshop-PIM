from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QCheckBox,
    QPushButton,
    QSplitter,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator

from app.gui.theme import APP_STYLE
from app.gui.widgets.sidebar import Sidebar
from app.gui.widgets.kpi_cards import KpiCards
from app.gui.widgets.product_table import ProductTable
from app.gui.widgets.product_detail import ProductDetail
from app.services.export_filter_service import ProductExportFilters


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Weldingshop Product Platform")
        self.resize(1600, 900)
        self.setStyleSheet(APP_STYLE)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(10)

        self.sidebar = Sidebar()
        self.sidebar.setFixedWidth(200)
        root_layout.addWidget(self.sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(6)

        header = QLabel("Weldingshop Product Platform")
        header.setObjectName("PageTitle")
        header.setFixedHeight(32)

        subtitle = QLabel("Productbeheer, AI-content, kwaliteitscontrole en Shopify-publicatie")
        subtitle.setObjectName("Muted")
        subtitle.setFixedHeight(22)

        self.kpis = KpiCards()
        self.kpis.setFixedHeight(95)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Zoek op SKU, titel of EAN...")
        self.search.setFixedHeight(42)

        self.filter_no_photo = QCheckBox("Geen uitvoer als product geen foto heeft")
        self.filter_missing_price = QCheckBox("Geen uitvoer als prijs ontbreekt")
        self.filter_zero_price = QCheckBox("Geen uitvoer als prijs 0 is")
        self.filter_missing_ean = QCheckBox("Geen uitvoer als EAN ontbreekt")
        self.filter_missing_sku = QCheckBox("Geen uitvoer als SKU ontbreekt")

        self.min_price = QLineEdit()
        self.min_price.setPlaceholderText("Geen minimum")
        self.min_price.setFixedHeight(36)
        validator = QDoubleValidator(0, 999999999, 2, self)
        validator.setNotation(QDoubleValidator.StandardNotation)
        self.min_price.setValidator(validator)

        min_price_label = QLabel("Alleen producten duurder dan:")
        self.apply_filters = QPushButton("Filter toepassen")
        self.apply_filters.setFixedHeight(36)

        filter_panel = QWidget()
        filter_layout = QGridLayout(filter_panel)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setHorizontalSpacing(18)
        filter_layout.setVerticalSpacing(6)
        filter_layout.addWidget(self.filter_no_photo, 0, 0)
        filter_layout.addWidget(self.filter_missing_price, 0, 1)
        filter_layout.addWidget(self.filter_zero_price, 0, 2)
        filter_layout.addWidget(self.filter_missing_ean, 1, 0)
        filter_layout.addWidget(self.filter_missing_sku, 1, 1)
        filter_layout.addWidget(min_price_label, 1, 2)
        filter_layout.addWidget(self.min_price, 1, 3)
        filter_layout.addWidget(self.apply_filters, 1, 4)
        filter_layout.setColumnStretch(4, 1)

        self.product_table = ProductTable()
        self.product_detail = ProductDetail()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.product_table)
        splitter.addWidget(self.product_detail)
        splitter.setSizes([900, 650])
        splitter.setChildrenCollapsible(False)

        content_layout.addWidget(header)
        content_layout.addWidget(subtitle)
        content_layout.addWidget(self.kpis)
        content_layout.addWidget(self.search)
        content_layout.addWidget(filter_panel)
        content_layout.addWidget(splitter, 1)

        root_layout.addWidget(content, 1)

        self.setCentralWidget(root)

        self.search.textChanged.connect(self._reload_products)
        self.apply_filters.clicked.connect(self._reload_products)
        self.product_table.product_selected.connect(self.product_detail.load_product)

        self._reload_products()

    def _current_export_filters(self) -> ProductExportFilters:
        raw_min_price = self.min_price.text().strip().replace(",", ".")
        try:
            min_price = float(raw_min_price) if raw_min_price else None
        except ValueError:
            min_price = None

        return ProductExportFilters(
            exclude_without_photo=self.filter_no_photo.isChecked(),
            exclude_missing_price=self.filter_missing_price.isChecked(),
            exclude_zero_price=self.filter_zero_price.isChecked(),
            exclude_missing_ean=self.filter_missing_ean.isChecked(),
            exclude_missing_sku=self.filter_missing_sku.isChecked(),
            min_price=min_price,
        )

    def _reload_products(self):
        self.product_table.load_products(self.search.text(), self._current_export_filters())
