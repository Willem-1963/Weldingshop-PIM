from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
from PySide6.QtCore import Signal
from app.repositories.product_repository import ProductRepository
from app.repositories.image_repository import ImageRepository


def format_price(value):
    if value is None or value == "":
        return "-"
    try:
        return f"€ {float(value):.2f}".replace(".", ",")
    except Exception:
        return str(value)


class ProductTable(QTableWidget):
    product_selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.setColumnCount(7)
        self.setHorizontalHeaderLabels(["SKU", "Titel", "Prijs", "EAN", "Foto", "AI", "Shopify"])
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.cellClicked.connect(self._on_cell_clicked)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.verticalHeader().setVisible(False)

    def load_products(self, query: str = ""):
        products = ProductRepository.search(query, limit=150)
        self.setRowCount(len(products))
        for row, product in enumerate(products):
            title = product.ai_title or product.source_title or ""
            image_count = ImageRepository.count_by_sku(product.sku)
            self.setItem(row, 0, QTableWidgetItem(product.sku or ""))
            self.setItem(row, 1, QTableWidgetItem(title))
            self.setItem(row, 2, QTableWidgetItem(format_price(product.price)))
            self.setItem(row, 3, QTableWidgetItem(product.ean or "-"))
            self.setItem(row, 4, QTableWidgetItem("✓" if image_count else "-"))
            self.setItem(row, 5, QTableWidgetItem("✓" if product.ai_generated else "-"))
            self.setItem(row, 6, QTableWidgetItem("✓" if product.shopify_exported else "-"))
        if products:
            self.selectRow(0)
            self.product_selected.emit(products[0].sku)

    def _on_cell_clicked(self, row: int, column: int):
        item = self.item(row, 0)
        if item:
            self.product_selected.emit(item.text())
