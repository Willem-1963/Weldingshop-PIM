# Certilas → Shopify productfamiliecontract

Vanaf 3 augustus 2026 is `product_families.family_json` de canonieke bron voor
de commerciële Shopify-opbouw van Certilas-producten.

## Vaste regels

1. Eén PIM-productfamilie wordt één Shopify-product.
2. De familietitel wordt de Shopify-producttitel; een SKU mag nooit een
   bestaande titel vervangen wanneer PIM geen titel levert.
3. De familie-HTML is leidend voor de producttekst, inclusief aanwezige
   technische pictogrammen. Een lege PIM-tekst mag bestaande Shopify-HTML niet
   wissen.
4. Diameter en verpakking worden variantopties. De betekenis van gebruikte
   verpakkingscodes wordt opgeslagen in `custom.verpakkingsopmerking`.
   De verpakkingsoptie begint altijd met de vaste zichtbare code, bijvoorbeeld
   `D100 – kleine spoel 1 kg`, `D200 – kunststof spoel 5 kg`,
   `D300 – spoel 15 kg`, `BS300 – draadkorf 15 kg` of `Koker – 5 kg`.
   Variantmaten gebruiken altijd een punt als decimaalteken en geen spatie voor
   de eenheid: `0.8mm`, `1.0mm`, `1.2mm`, enzovoort.
5. De synchronisatie stopt vóór een mutatie wanneer:
   - één Shopify-product meerdere PIM-families bevat;
   - één PIM-familie over meerdere Shopify-producten verdeeld is;
   - familievarianten en losse PIM-artikelen in één Shopify-product staan.
6. Nieuwe producten blijven onder het ingestelde `existing_only`-beleid vallen;
   familieopbouw geeft geen toestemming om het volledige assortiment aan te
   maken.

## Herstel en controle

- Droge en uitgevoerde herstelruns worden onder `data/audit/` vastgelegd.
- `scripts/rebuild_certilas_shopify_families.py` draait standaard alleen als
  dry-run; mutaties vereisen expliciet `--execute`.
- Regressietests staan in `test_shopify_family_canonical.py` en
  `test_shopify_title_fallback.py`.
