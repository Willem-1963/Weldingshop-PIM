# Weldingshop AI Product Factory

Eerste leverancier: SP Tools.

## Installatie

```powershell
pip install -r requirements.txt
```

## Bestanden

Zorg dat deze bestanden bestaan:

```text
data/input/SP Tools bruto prijslijst 2026 v3.xlsx
data/input/productdetails.csv
```

## Import draaien

```powershell
py main.py import-sp-tools
```

## Shopify CSV maken

```powershell
py main.py export-shopify --limit 100
```

## Alles in één keer

```powershell
py main.py run-all --limit 100
```

## Shopify instellingen

De export gebruikt standaard:

- Status: draft
- Variant Inventory Policy: continue
- Published: FALSE
