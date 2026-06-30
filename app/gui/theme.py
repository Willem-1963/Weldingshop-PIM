APP_STYLE = """
QMainWindow {
    background: #f3f4f6;
}
QWidget {
    font-family: Segoe UI, Arial, sans-serif;
    font-size: 13px;
    color: #111827;
}
QListWidget {
    background: #0f172a;
    color: #e5e7eb;
    border: none;
    padding: 10px;
    font-size: 14px;
}
QListWidget::item {
    padding: 12px;
    margin: 3px 0;
    border-radius: 8px;
}
QListWidget::item:selected {
    background: #2563eb;
    color: white;
}
QLineEdit {
    padding: 10px;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    background: white;
    font-size: 14px;
}
QTableWidget {
    background: white;
    border: 1px solid #d1d5db;
    border-radius: 10px;
    gridline-color: #e5e7eb;
    selection-background-color: #dbeafe;
    selection-color: #111827;
}
QHeaderView::section {
    background: #f9fafb;
    padding: 8px;
    border: none;
    border-bottom: 1px solid #d1d5db;
    font-weight: 600;
}
QTabWidget::pane {
    border: 1px solid #d1d5db;
    border-radius: 10px;
    background: white;
}
QTabBar::tab {
    background: #e5e7eb;
    padding: 9px 14px;
    margin-right: 2px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}
QTabBar::tab:selected {
    background: white;
    font-weight: 600;
}
QTextBrowser, QTextEdit {
    background: white;
    border: none;
    padding: 8px;
}
QPushButton {
    background: #2563eb;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 9px 14px;
    font-weight: 600;
}
QPushButton:hover {
    background: #1d4ed8;
}
QPushButton:disabled {
    background: #9ca3af;
}
QFrame#Card {
    background: white;
    border: 1px solid #d1d5db;
    border-radius: 12px;
}
QLabel#PageTitle {
    font-size: 24px;
    font-weight: 700;
}
QLabel#Muted {
    color: #6b7280;
}
QLabel#KpiValue {
    font-size: 24px;
    font-weight: 700;
}
QLabel#KpiTitle {
    color: #6b7280;
    font-weight: 600;
}
"""
