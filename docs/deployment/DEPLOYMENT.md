# Deployment

## Centrale projectmap

```text
/srv/ai-product-factory
```

OpenClaw heeft toegang tot deze projectmap.

## Publieke endpoints

- Dashboard: `https://pim.weldingshop.nl`
- OpenClaw: `https://ai.weldingshop.nl`

## Infrastructuur

- Ubuntu 24.04
- Docker
- Portainer
- Nginx
- Let's Encrypt SSL
- Basic Authentication
- Streamlit Dashboard
- OpenClaw
- SQLite
- SQLAlchemy
- OpenAI Responses API

## Dashboard

Streamlit is het gekozen dashboardframework.

Het dashboard draait achter Nginx en SSL op:

```text
https://pim.weldingshop.nl
```

## Beveiliging

Basic Authentication is toegevoegd.

Nieuwe dashboards zijn standaard niet publiek toegankelijk.

## Werkafspraken

- Windows Terminal
- Server Shell met actieve `.venv`
- Python Code
- OpenClaw

## Status

De infrastructuur is gereed.

v0.6 AI Database Integratie is klaar.

De volgende sprint is v0.7 Shopify AI Export.
