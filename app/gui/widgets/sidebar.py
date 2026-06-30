from PySide6.QtWidgets import QListWidget


class Sidebar(QListWidget):
    def __init__(self):
        super().__init__()
        self.addItems([
            "Dashboard",
            "Producten",
            "AI Queue",
            "Shopify",
            "Leveranciers",
            "Import",
            "Rapportages",
            "Instellingen",
        ])
        self.setFixedWidth(205)
        self.setCurrentRow(1)
