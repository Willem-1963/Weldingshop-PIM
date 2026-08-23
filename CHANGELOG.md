# CHANGELOG

## 2026-07-05

### Infrastructuur

- Ubuntu 24.04 als serverbasis vastgelegd.
- Docker en Portainer opgenomen in de projectstatus.
- Nginx reverse proxy ingericht.
- Let's Encrypt SSL geconfigureerd.
- Dashboard draait op `https://pim.weldingshop.nl`.
- Basic Authentication toegevoegd.
- OpenClaw gekoppeld aan de projectmap `/root/weldingshop-pim`.
- OpenClaw bereikbaar via `https://ai.weldingshop.nl`.

### Dashboard

- Qt verlaten als GUI-richting.
- Streamlit gekozen als webgebaseerde GUI.
- Streamlit Dashboard opgenomen als centrale gebruikersinterface.
- GUI-first ontwikkeling officieel vastgelegd.

### AI en database

- OpenAI Responses API opgenomen als AI-provider.
- SQLite en SQLAlchemy opgenomen als databasebasis.
- v0.6 AI Database Integratie afgerond.
- AI-resultaten worden opgeslagen via `ProductService.mark_ai_generated()`.
- AI-databasevelden vastgelegd: `ai_title`, `html_description` en `ai_generated`.
- AI-flow vastgelegd als Product -> Prompt Builder -> OpenAI -> JSON Validator -> ProductService -> Database.

### Documentatie

- Documentatiestructuur uitgebreid.
- `docs/INDEX.md` toegevoegd en bijgewerkt als centrale documentatie-index.
- Templates toegevoegd in `templates/`.
- `docs/development/PROJECT_CONSTITUTION.md` toegevoegd.
- Project Constitution vastgelegd met 10 projectprincipes.
- OpenClaw-documentatietaak vastgelegd.

### Projectregels

- GUI First vastgelegd.
- Service First vastgelegd.
- JSON First vastgelegd.
- AI Safety vastgelegd.
- Documentation First vastgelegd.
- Template First vastgelegd.
- Feature Documentation vastgelegd.
- Test First vastgelegd.
- Security First vastgelegd.
- Architecture Above Speed vastgelegd.

### Volgende sprint

- v0.7 Shopify AI Export is de volgende sprint.
