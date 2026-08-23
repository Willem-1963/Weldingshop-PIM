# AI Product Factory

AI Product Factory wordt het centrale PIM-platform voor Weldingshop: productdata beheren, AI-verrijking uitvoeren en Shopify-export voorbereiden vanuit één servergebaseerde omgeving.

## Start hier

- `PROJECT_STATUS.md` - Actuele status, infrastructuur en volgende sprint.
- `CHANGELOG.md` - Wijzigingen en besluiten van vandaag.
- `docs/INDEX.md` - Volledige documentatie-index.
- `templates/` - Standaardtemplates voor nieuwe documentatie.

## Centrale omgeving

- Projectmap: `/srv/ai-product-factory`
- Dashboard: `https://pim.weldingshop.nl`
- OpenClaw: `https://ai.weldingshop.nl`
- GUI: Streamlit
- Database: SQLite via SQLAlchemy
- AI: OpenAI Responses API
- Reverse proxy: Nginx met Let's Encrypt SSL
- Beveiliging: Basic Authentication

## Huidige status

De infrastructuur is gereed. OpenClaw heeft toegang tot `/srv/ai-product-factory`.

v0.6 AI Database Integratie is klaar.

De volgende sprint is v0.7 Shopify AI Export.

## Architectuur

De applicatie volgt deze hoofdopbouw:

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

AI-output loopt altijd via gevalideerde JSON:

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

## Projectregels

De officiële ontwikkelprincipes staan in:

```text
docs/development/PROJECT_CONSTITUTION.md
```

Belangrijke afspraken:

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

## Documentatie

Gebruik `docs/INDEX.md` als centrale ingang voor alle documentatie.

Gebruik `templates/` voor nieuwe feature-, module-, database- en API-documentatie.
