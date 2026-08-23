# Architecture

## Centrale omgeving

- Projectmap: `/srv/ai-product-factory`
- Dashboard: `https://pim.weldingshop.nl`
- OpenClaw: `https://ai.weldingshop.nl`
- Server: Ubuntu 24.04
- Database: SQLite via SQLAlchemy
- AI: OpenAI Responses API

## Applicatiearchitectuur

De architectuur volgt deze hoofdopbouw:

```text
Frontend
↓
Streamlit
↓
Services
↓
Repositories
↓
SQLite
↓
OpenAI
↓
Shopify
↓
Leveranciers
```

## Lagen

- Frontend: Streamlit webinterface.
- Services: centrale applicatielogica.
- Repositories: gecontroleerde toegang tot databasegegevens.
- SQLite: lokale relationele database.
- OpenAI: AI-verrijking via Responses API.
- Shopify: exportdoel voor productdata.
- Leveranciers: brondata voor producten.

## AI-verwerking

AI-verwerking loopt altijd via:

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

Ruwe AI-output wordt niet direct opgeslagen. Alleen gevalideerde JSON wordt verwerkt via `ProductService`.

## Serverarchitectuur

- Ubuntu 24.04 draait de applicatieomgeving.
- Docker en Portainer zijn beschikbaar voor containerbeheer.
- Nginx verzorgt reverse proxy.
- Let's Encrypt levert SSL.
- Basic Authentication beschermt het dashboard.
- Streamlit levert de webinterface.
- OpenClaw heeft toegang tot `/srv/ai-product-factory`.

## Richting

v0.6 AI Database Integratie is klaar.

De volgende sprint is v0.7 Shopify AI Export.
