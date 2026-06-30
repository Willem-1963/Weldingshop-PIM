# Release 0.4A - Feature 001 Product Service

## Doel
Deze release introduceert de eerste echte servicelaag van Weldingshop PIM.

Vanaf nu hoeven AI, Shopify, rapportages en later de GUI niet meer rechtstreeks databasequeries te schrijven. Zij gebruiken `ProductService`.

## Toegevoegd
- `app/services/product_service.py`
- `app/services/product_service_cli.py`
- `app/services/__init__.py`

## Testcommando's
```powershell
py -m app.services.product_service_cli 193GE-10
py -m app.services.product_service_cli --search ratel
py -m app.services.product_service_cli --missing-description --limit 10
py -m app.services.product_service_cli --missing-images --limit 10
```

## Git commit
```powershell
git add .
git commit -m "Voeg Product Service toe"
git push
```
