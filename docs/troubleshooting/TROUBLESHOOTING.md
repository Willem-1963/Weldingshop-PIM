# Troubleshooting

## Projectmap controleren

Werk uitsluitend in:

```text
/srv/weldingshop-pim
```

OpenClaw moet toegang hebben tot deze map.

## Dashboard

Dashboard URL:

```text
https://pim.weldingshop.nl
```

Controleer bij problemen:

- Draait Streamlit?
- Wijst Nginx naar de juiste service?
- Is SSL actief voor `pim.weldingshop.nl`?
- Werkt Basic Authentication?

## Server

Controleer de servercomponenten:

- Ubuntu 24.04
- Docker
- Portainer
- Nginx
- Let's Encrypt SSL
- Streamlit
- OpenClaw

## AI-output

Controleer bij ontbrekende AI-resultaten:

- Is het product opgehaald?
- Is de Prompt Builder uitgevoerd?
- Is OpenAI aangeroepen?
- Is JSON-validatie geslaagd?
- Is `ProductService.mark_ai_generated()` aangeroepen?
- Zijn `ai_title`, `html_description` en `ai_generated` gevuld?

## Database

Controleer:

- SQLite is bereikbaar.
- SQLAlchemy werkt.
- Services gebruiken Repositories.
- GUI en CLI benaderen de database niet rechtstreeks.

## OpenClaw

OpenClaw URL:

```text
https://ai.weldingshop.nl
```

OpenClaw is verantwoordelijk voor documentatie bijhouden, templates gebruiken, changelog bijwerken, projectstatus bijwerken, index bijwerken en featuredocumentatie maken.
