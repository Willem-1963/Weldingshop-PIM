# Weldingshop Bedankvoordeel

## Pilotregels

Nieuwe betaalde machineorders vanaf de livegang kwalificeren wanneer:

- verkoopkanaal Online Store (`source_name=web`) of Shopify POS (`source_name=pos`) is;
- een Shopify-klant met e-mailadres aan de order is gekoppeld;
- minimaal één machineproduct expliciet is gemarkeerd als MIG/MAG, TIG of plasma;
- de kwalificerende machineregel geen korting heeft;
- de klant nog geen actief, niet-verlopen bedankvoordeel heeft.

De code:

- is persoonlijk en eenmalig;
- is 90 dagen geldig;
- geeft 10% op expliciet gemarkeerde accessoires/slijtdelen;
- vereist minimaal €50 aan werkelijk kortingwaardige regels;
- geeft maximaal €40 korting;
- is niet combineerbaar;
- werkt alleen online; een POS-machineaankoop kan de code wel uitgeven;
- faalt gesloten bij ontbrekende kostprijs, ontbrekende minimumprijs, niet-EUR of te krappe marge.

## Beschermde minimumprijs

Voor elke gemarkeerde variant wordt `custom.return_discount_min_price` berekend als kostprijs × 1,41. De Shopify Function beperkt de 10% korting per regel zodat de prijs nooit onder deze grens komt.

## Catalogusafbakening

Machinecollecties:

- `mig-mag-lasapparaten`
- `tig-lasapparaten`
- `plasmasnijmachines`

Accessoires/slijtdelen komen uitsluitend uit de vastgelegde MIG/MAG-, TIG- en plasmacollecties in `app/shopify/return_discount_catalog.py`. Nieuwe collectiemembers worden dagelijks gemarkeerd; producten buiten deze collecties krijgen geen voordeel.

## Uitgifte en e-mail

`app/shopify/return_discount.py` verwerkt `ORDERS_CREATE`, `ORDERS_PAID` en `ORDERS_CANCELLED` via de bestaande Shopify-webhookcallback. Bij kwalificatie wordt een unieke code onder de appkorting `Weldingshop Bedankvoordeel` aangemaakt, klant- en ordermetafields worden teruggelezen en de klant krijgt een e-mail.

POS-medewerkers moeten vóór betaling de klant en het e-mailadres koppelen. Zonder e-mailadres wordt geen code uitgegeven; deze kan later handmatig worden afgehandeld.

Een order waarop een bedankcode wordt gebruikt geeft geen nieuwe code. Annulering van de bronorder deactiveert een nog actief tegoed. Een volledige refund van de bronorder deactiveert het tegoed automatisch; partiële refunds worden tijdens de pilot handmatig beoordeeld.

## Onderdelen

- Shopify Function: `bedankvoordeel`
- Function-ID: `01a14a0a-0c20-7e32-a6ec-96369268fef8`
- Beheerde discountnode/seed: `BEDANKT-START`
- Productmarkeringen: `app/shopify/return_discount_catalog.py`
- Orderautomatisering: `app/shopify/return_discount.py`
- Journal: `data/database/return_discount.sqlite`
- Audits: `data/audit/return-discount-catalog/`
- Dagelijks onderhoud: `weldingshop-certilas-loyalty-maintenance.timer`

## Controle

- `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_return_discount.py`
- `PYTHONPATH=. .venv/bin/python scripts/sync_return_discount_catalog.py`
- `systemctl status weldingshop-pim-webhook.service`
- `systemctl status weldingshop-certilas-loyalty-maintenance.timer`
