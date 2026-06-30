# Module Product Core

## Doel
De Product Core module vormt het centrale productbeeld binnen Weldingshop PIM.

In plaats van losse databasequeries voor product, afbeeldingen en specificaties gebruikt de rest van de applicatie voortaan één centrale service.

## Belangrijkste onderdelen

- `ProductRecord`: read-only aggregate object voor productdata.
- `ProductCoreService`: haalt product + afbeeldingen + specificaties op.
- `SpecificationService`: centrale service voor specificaties.
- `product_core_cli.py`: testinterface via PowerShell.

## Waarom belangrijk?
Deze module wordt gebruikt door:

- GUI
- AI Engine
- Shopify Engine
- Quality Engine
- Reports

## Testcommando's

```powershell
py -m app.services.product_core_cli 193GE-10
py -m app.services.product_core_cli --search ratel
py -m app.services.product_core_cli --missing-content --limit 10
py -m app.services.product_core_cli --shopify-ready --limit 10
```
