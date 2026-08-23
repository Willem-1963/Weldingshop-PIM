# Weldingshop PIM - Project Status

Laatst bijgewerkt: 2026-07-05

## Projectstatus

Weldingshop PIM wordt het centrale PIM-platform voor Weldingshop.

v0.6 AI Database Integratie is klaar.

De volgende sprint is v0.7 Shopify AI Export.

## Centrale omgeving

- Centrale projectmap: `/root/weldingshop-pim`
- Dashboard: `https://pim.weldingshop.nl`
- OpenClaw: `https://ai.weldingshop.nl`

## Infrastructuur gereed

- ✓ Ubuntu 24.04
- ✓ Docker
- ✓ Portainer
- ✓ Nginx
- ✓ Let's Encrypt SSL
- ✓ OpenClaw
- ✓ OpenAI Responses API
- ✓ SQLite
- ✓ SQLAlchemy
- ✓ Streamlit Dashboard
- ✓ `https://pim.weldingshop.nl`
- ✓ Basic Authentication
- ✓ Documentatiestructuur
- ✓ Templates
- ✓ OpenClaw heeft toegang tot `/root/weldingshop-pim`

## Werkafspraken

- Windows Terminal
- Server Shell met actieve `.venv`
- Python Code
- OpenClaw
- GUI-first ontwikkeling
- Services-first architectuur
- Projectregels vastgelegd in `docs/development/PROJECT_CONSTITUTION.md`

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

AI-resultaten worden opgeslagen via:

```text
ProductService.mark_ai_generated()
```

Gebruikte databasevelden:

- `ai_title`
- `html_description`
- `ai_generated`

## Dashboard

Qt is verlaten.

Streamlit is gekozen als dashboardframework.

Dashboard URL:

```text
https://pim.weldingshop.nl
```

Basic Authentication is toegevoegd.

## Server

De serveromgeving bestaat uit Ubuntu 24.04, Docker, Portainer, Nginx, Let's Encrypt SSL, Streamlit en OpenClaw.

Nginx + SSL werkt voor:

```text
pim.weldingshop.nl
```

OpenClaw draait via:

```text
https://ai.weldingshop.nl
```

## OpenClaw taak

OpenClaw is verantwoordelijk voor:

- Documentatie onderhouden
- Templates gebruiken
- Changelog bijwerken
- Projectstatus bijwerken
- Index bijwerken
- Featuredocumentatie maken

## Volgende sprint

v0.7 Shopify AI Export:

- AI-velden gebruiken in Shopify-export.
- Export voorbereiden op `ai_title` en `html_description`.
- Exportstatus en controle toevoegen in de GUI.

## Documentatie-afspraak

Na iedere ontwikkelsessie worden bijgewerkt:

- `PROJECT_STATUS.md`
- `CHANGELOG.md`
- `docs/INDEX.md`
- Relevante documentatie
