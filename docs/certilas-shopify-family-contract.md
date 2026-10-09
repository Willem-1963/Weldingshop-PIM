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

## Minimale bestelhoeveelheid (9 oktober 2026)

- `KG Ceweld Unit` en `KG Ceweld Bundle` beschrijven verpakkingen; een
  bundelgewicht bewijst geen verplichte afname.
- De dealerportal is leidend: `min` en `step` van `txtCount_<sku>` plus het
  gewicht uit `changeWeightAmount` bepalen de minimale verkoopeenheid.
  Minimumgewicht = minimumaantal × kg per portalbesteleenheid.
- Portalgegevens worden met SKU, EAN, bron-URL en controledatum apart in
  `certilas_order_units` opgeslagen. Nieuwe Excel-imports wissen ze niet.
- Shopify-aantal 1 staat voor één minimumhoeveelheid. Verkoopprijs en
  kostprijs worden naar dat gewicht omgerekend; het verzendgewicht is het
  minimumgewicht. De variantmelding benoemt deze hoeveelheid expliciet.
- Verhoudingen hoeven geen hele aantallen fysieke verpakkingen te zijn:
  artikel 10022 heeft een minimumafname van 13,2 kg, ongeacht het fysieke
  verpakkingsgewicht van 2 kg in de bron.
- Artikelen zonder gecontroleerde portalregel of met een afwijkend EAN
  worden niet automatisch naar een veronderstelde minimumhoeveelheid omgezet.
- Expliciete winkeluitzonderingen in `certilas_pack_overrides` gaan vóór de
  portalminimumafname. Deze leggen per SKU en EAN het pakgewicht, de reden en
  bevestigingsdatum vast. Excel- en portalimports overschrijven ze niet.
  E Corten 10382 (2,5 mm, 2 kg) en 10383 (3,2 mm, 2,6 kg) worden per pak
  verkocht. De variantmelding, prijs, kostprijs en het gewicht volgen dat pak.
  De oorspronkelijke leverancierminimumafname blijft apart bewaard.
- `scripts/collect_certilas_order_units.py` verzamelt gegevens zonder
  winkelwagenwijzigingen. `scripts/apply_certilas_order_units.py` maakt
  standaard een plan; `--apply` schrijft het gecontroleerde resultaat en
  bewaart herstelbestanden en een verificatierapport onder `data/audit/`.

## Herstelbestanden

- Droge en uitgevoerde herstelruns worden onder `data/audit/` vastgelegd.
- `scripts/rebuild_certilas_shopify_families.py` draait standaard alleen als
  dry-run; mutaties vereisen expliciet `--execute`.
- Regressietests staan in `test_shopify_family_canonical.py` en
  `test_shopify_title_fallback.py`.
