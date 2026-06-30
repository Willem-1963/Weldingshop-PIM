from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QSplitter,
)
from PySide6.QtCore import Qt

from app.gui.theme import APP_STYLE
from app.gui.widgets.sidebar import Sidebar
from app.gui.widgets.kpi_cards import KpiCards
from app.gui.widgets.product_table import ProductTable
from app.gui.widgets.product_detail import ProductDetail


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
        content_layout.addWidget(splitter, 1)

        root_layout.addWidget(content, 1)

        self.setCentralWidget(root)

        self.search.textChanged.connect(self.product_table.load_products)
        self.product_table.product_selected.connect(self.product_detail.load_product)

        self.product_table.load_products("")