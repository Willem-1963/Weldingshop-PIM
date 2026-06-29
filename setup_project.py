from pathlib import Path

folders = [
    "app/database",
    "app/importers",
    "app/suppliers/sp_tools",
    "app/shopify",
    "app/ai",
    "app/utils",
    "data/input",
    "data/output",
    "data/database",
    "data/cache",
    "logs",
    "config",
    "tests",
]

files = [
    "app/__init__.py",
    "app/database/__init__.py",
    "app/importers/__init__.py",
    "app/suppliers/__init__.py",
    "app/suppliers/sp_tools/__init__.py",
    "app/shopify/__init__.py",
    "app/ai/__init__.py",
    "app/utils/__init__.py",
    "main.py",
    "README.md",
]

for folder in folders:
    Path(folder).mkdir(parents=True, exist_ok=True)

for file in files:
    Path(file).touch(exist_ok=True)

Path("config/settings.example.env").write_text(
    "SHOPIFY_SHOP=\nSHOPIFY_ACCESS_TOKEN=\nOPENAI_API_KEY=\n",
    encoding="utf-8",
)

Path("README.md").write_text(
    "# Weldingshop AI Product Factory\n\nEerste leverancier: SP Tools.\n",
    encoding="utf-8",
)

print("Projectstructuur is aangemaakt.")
