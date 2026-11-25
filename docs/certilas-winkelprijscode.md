# Certilas winkelprijscode

## Gedrag

Een betaalde Online Store-bestelling (`source_name=web`) met minimaal één Certilas/CEWELD-regel tegen de volledige webprijs levert één persoonlijke code op. De code:

- verlaagt alleen Certilas-regels naar het vaste POS-prijsmetafield `custom.certilas_pos_prijs`;
- is gekoppeld aan de Shopify-klant via `custom.certilas_winkelprijs_code`;
- werkt alleen zolang `custom.certilas_winkelprijs_actief=true`;
- verloopt 365 dagen na uitgifte;
- wordt gedeactiveerd zodra een betaalde bestelling de persoonlijke code gebruikt;
- kan niet met andere kortingen worden gecombineerd.

Een bestelling waarop al een persoonlijke Certilas-code is gebruikt, geeft geen nieuwe code uit. Niet-webshop-, onbetaalde en reeds afgeprijsde orders worden overgeslagen.

## Onderdelen

- Shopify Function-app: `Weldingshop Inkoophistorie`, functionhandle `certilas-winkelprijs`.
- Discount Function-instellingen: `certilas-winkelprijs-instellingen`.
- Webhookverwerking: `app/shopify/certilas_loyalty.py` via `app/shopify/webhook_server.py`.
- POS-prijssync: `app/shopify/certilas_pos_prices.py`.
- Dagelijks onderhoud: `weldingshop-certilas-loyalty-maintenance.timer`.
- Journal: `data/database/certilas_loyalty.sqlite`.
- POS-prijsaudit: `data/audit/certilas-pos-price-metafields/`.

## Handmatig uitgeven in Shopify Admin

Open de gewenste klant in Shopify Admin en kies via **Meer acties** de actie **Certilas-winkelprijscode uitgeven**. Kies 30, 90 of 365 dagen en maak de code aan. Kopieer de getoonde code en verstuur hem handmatig.

De actie:

- koppelt de code uitsluitend aan de geselecteerde Shopify-klant;
- blokkeert een tweede code zolang een bestaande code nog actief is;
- maakt geen klantmail en wijzigt geen prijs of bestelling;
- controleert de aanmaak en klantmetafields direct via Shopify-readback.

De Shopify Function controleert de vervaldatum bij iedere winkelwagenberekening. Handmatig uitgegeven codes verlopen daardoor ook zonder lokaal PIM-journal veilig.

## Shopify-webhooks

De bestaande callback ontvangt `ORDERS_CREATE`, `ORDERS_PAID`, `ORDERS_CANCELLED` en `REFUNDS_CREATE`.

Uitgifte en inwisseling zijn idempotent op Shopify-order-ID. De webhook geeft bij een fout HTTP 500 terug, zodat Shopify opnieuw bezorgt zonder een nieuwe code te genereren.

## Veiligheidsregels

- Ontbrekende, nul- of hogere POS-prijs geeft geen korting.
- Alleen EUR-regels worden verlaagd.
- De Shopify-POS-prijslijst zelf wordt niet gewijzigd.
- De dagelijkse sync schrijft alleen gewijzigde, positieve POS-prijzen.
- Codes blijven als redeemcode bestaan; klantmetafield `actief` bepaalt éénmaligheid en de Function controleert daarnaast `geldig_tot` tegen de actuele winkeldatum.

## Controle

- `systemctl status weldingshop-pim-webhook.service`
- `systemctl status weldingshop-certilas-loyalty-maintenance.timer`
- `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_certilas_loyalty.py`
- `PYTHONPATH=. .venv/bin/python scripts/sync_certilas_pos_price_metafields.py`
