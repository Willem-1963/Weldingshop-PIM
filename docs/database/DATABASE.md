# Database

## Basis

AI Product Factory gebruikt SQLite met SQLAlchemy.

Database-interactie loopt via Repositories en Services. GUI en CLI mogen de database niet rechtstreeks benaderen.

## AI-velden

De AI-integratie gebruikt deze databasevelden:

- `ai_title`
- `html_description`
- `ai_generated`

## Opslag

AI-resultaten worden opgeslagen via:

```text
ProductService.mark_ai_generated()
```

## Betekenis

- `ai_title`: door AI gegenereerde producttitel.
- `html_description`: door AI gegenereerde HTML-productomschrijving.
- `ai_generated`: markering dat het product AI-verrijking heeft.

## Dataveiligheid

- AI schrijft nooit rechtstreeks naar de database.
- Alleen gevalideerde JSON wordt opgeslagen.
- Services zijn de centrale toegangspoort voor wijzigingen.

## Status

v0.6 AI Database Integratie is klaar.

De volgende stap is v0.7 Shopify AI Export, waarin deze AI-velden gebruikt worden voor export.
