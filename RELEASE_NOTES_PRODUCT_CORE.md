# Release Notes - Product Core Module

## Toegevoegd
- ProductRecord aggregate object
- ProductCoreService
- SpecificationService
- Product Core CLI
- Documentatie voor Product Core

## Doel
Deze module centraliseert productdata zodat AI, Shopify, GUI en rapportages dezelfde productlogica gebruiken.

## Test
Voer uit:

```powershell
py -m app.services.product_core_cli 193GE-10
py -m app.services.product_core_cli --search ratel
py -m app.services.product_core_cli --missing-content --limit 10
py -m app.services.product_core_cli --shopify-ready --limit 10
```
