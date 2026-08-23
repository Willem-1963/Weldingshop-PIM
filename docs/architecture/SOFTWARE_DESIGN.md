# Software Design

## Visie

Weldingshop PIM wordt het centrale PIM-platform voor Weldingshop.

Het platform brengt productdata, leveranciersdata, AI-verrijking, databasebeheer, dashboardbediening en Shopify-export samen in één onderhoudbare serveromgeving.

## Doel

Weldingshop PIM ondersteunt:

- Productdata beheren.
- Producten verrijken met AI.
- Gevalideerde AI-output opslaan.
- Productinformatie voorbereiden voor Shopify.
- Dashboardgestuurd werken via Streamlit.

De centrale projectmap is:

```text
/srv/weldingshop-pim
```

## Ontwikkelprincipes

De officiële principes staan in:

```text
docs/development/PROJECT_CONSTITUTION.md
```

Belangrijkste richting:

- GUI First
- Service First
- JSON First
- AI Safety
- Documentation First
- Template First
- Feature Documentation
- Test First
- Security First
- Architecture Above Speed

## Hoofdmodules

- Frontend: Streamlit Dashboard.
- Services: applicatielogica en orchestration.
- Repositories: database-interactie.
- Database: SQLite via SQLAlchemy.
- AI Engine: Prompt Builder, OpenAI Responses API en JSON Validator.
- ProductService: gecontroleerde opslag van AI-resultaten.
- Shopify Export: volgende sprint, v0.7 Shopify AI Export.
- Leveranciersimport: brondata voor productverwerking.

## AI-flow

```text
Product
↓
Prompt Builder
↓
OpenAI
↓
JSON Validator
↓
ProductService
↓
Database
```

AI-resultaten worden opgeslagen via `ProductService.mark_ai_generated()`.

## Ontwerpkeuzes

- Qt is verlaten.
- Streamlit is gekozen voor de GUI.
- GUI en CLI benaderen de database niet rechtstreeks.
- Alle logica loopt via Services.
- AI schrijft nooit rechtstreeks naar de database.
- AI-output is uitsluitend gevalideerde JSON.
- Nieuwe documentatie gebruikt templates.
- Nieuwe features krijgen eigen featuredocumentatie.
