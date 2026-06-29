from __future__ import annotations

import typer
from rich.console import Console

from app.importers.import_sp_tools import run_import
from app.shopify.export_csv import export_shopify_csv

app = typer.Typer()
console = Console()


@app.command()
def import_sp_tools():
    """Importeer SP Tools Excel + productdetails.csv naar SQLite."""
    run_import()


@app.command()
def export_shopify(limit: int = 100, output: str = "shopify_batch_001.csv"):
    """Exporteer Shopify CSV vanuit de database."""
    path = export_shopify_csv(limit=limit, output_name=output)
    console.print(f"[green]Shopify CSV gemaakt:[/green] {path}")


@app.command()
def run_all(limit: int = 100):
    """Importeer data en maak direct een eerste Shopify CSV."""
    run_import()
    path = export_shopify_csv(limit=limit)
    console.print(f"[green]Klaar:[/green] {path}")


if __name__ == "__main__":
    app()
