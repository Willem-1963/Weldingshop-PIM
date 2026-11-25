# Certilas POS-winkelprijs op conceptbestellingen

De webshopvariant behoudt zijn vaste webshopprijs. Alleen een Shopify-conceptbestelling met tag winkelprijs krijgt voor gekoppelde Certilas-varianten de vaste POS-winkelprijs uit PriceList gid://shopify/PriceList/31914918217 als priceOverride.

## Directe route

- Shopify-webhooks: DRAFT_ORDERS_CREATE en DRAFT_ORDERS_UPDATE.
- Endpoint: https://pim.weldingshop.nl/shopify/webhooks/inventory.
- Handler: app/shopify/certilas_draft_pricing.py.
- Service: weldingshop-pim-webhook.service.
- Geen tag: geen wijziging.
- Alleen EUR en custom.certilas_product=true.
- Maatwerkregels, bundels, regelkortingen en concepten met meer dan 250 regels worden volledig overgeslagen.
- Niet-Certilas-regels en bestaande overrides blijven behouden.
- De eigen update triggert opnieuw een webhook, maar de tweede verwerking is idempotent (noop).

## Shopify Admin-weergave

Na een achtergrondupdate kan Shopify Admin kort nog de oude globale webshop/catalogusprijs tonen terwijl het subtotaal al is bijgewerkt. Na de volgende UI-verversing toont de productregel de POS-winkelprijs uit DraftOrderLineItem.priceOverride en originalUnitPriceSet. #D9564 liet eerst €198 zien met subtotaal €270 en daarna correct €135 per stuk en €270 voor twee stuks. De variant/webshopprijs zelf blijft €198.

## Bewijs 26 oktober 2026

- #D9564: variantprijs €198,00; priceOverride en originalUnitPriceSet beide €135,00; twee stuks geven subtotaal €270,00.
- Twee tijdelijke end-to-endconcepten werden in 2,848 en 3,548 seconden verwerkt en volledig verwijderd.
- Tests: tests/test_certilas_draft_pricing.py plus bestaande Certilas-tests, 10/10 groen.
