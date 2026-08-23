# Streamlit

## Dashboard

Streamlit is gekozen als webinterface voor Weldingshop PIM.

Dashboard URL:

```text
https://pim.weldingshop.nl
```

## Status

- Streamlit Dashboard is actief als centrale GUI.
- Qt is verlaten.
- GUI-first ontwikkeling is het standaardprincipe.
- Basic Authentication is toegevoegd.

## Rol

Streamlit is de primaire gebruikersinterface voor:

- Producten bekijken.
- Productdetails beheren.
- AI-verrijking aansturen.
- Exportstatus inzichtelijk maken.
- Toekomstige Shopify AI Export bedienen.

## Architectuurafspraak

Streamlit mag de database niet rechtstreeks benaderen.

Dashboardacties lopen via Services.

## Centrale projectmap

```text
/srv/weldingshop-pim
```
