# Shopify

## Status

De volgende sprint is v0.7 Shopify AI Export.

## Doel v0.7

Shopify-export moet de AI-resultaten uit de database gebruiken.

Belangrijke velden:

- `ai_title`
- `html_description`
- `ai_generated`

## Verwachte flow

```text
Database
↓
Services
↓
Shopify Export
↓
Shopify
```

## Relatie met AI

AI-resultaten worden eerst gevalideerd en opgeslagen via:

```text
ProductService.mark_ai_generated()
```

Daarna kan Shopify Export deze velden gebruiken.

## Architectuurafspraak

Shopify Export gebruikt Services en Repositories. Exportlogica mag niet rechtstreeks buiten de projectarchitectuur om databasewijzigingen uitvoeren.
