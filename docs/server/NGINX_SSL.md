# Nginx en SSL

## Status

Nginx + Let's Encrypt SSL werkt voor:

```text
pim.weldingshop.nl
```

## Servercomponenten

- Ubuntu 24.04
- Docker
- Portainer
- Nginx
- Let's Encrypt SSL
- Basic Authentication
- Streamlit
- OpenClaw

## Routes

- Dashboard: `https://pim.weldingshop.nl`
- OpenClaw: `https://ai.weldingshop.nl`

## Beveiliging

Nieuwe dashboards zijn standaard niet publiek toegankelijk.

Basic Authentication is toegevoegd voor het dashboard.

Authenticatie en beveiliging worden direct meegenomen bij nieuwe dashboards of serverroutes.

## Rol

Nginx verzorgt reverse proxy, publieke toegang en SSL-afhandeling voor de webinterfaces.

## Projectmap

De applicatie staat centraal in:

```text
/srv/weldingshop-pim
```
