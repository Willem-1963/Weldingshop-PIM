# Rhodius: VE en GTIN voor Shopify

## Doel
Eén besteld Shopify-artikel vertegenwoordigt de VE uit het PIM. De twee oorspronkelijke GTIN-velden blijven bewaard.

## Status
Geïmplementeerd op 2026-09-18. Negen afzonderlijke Shopify-testconcepten met TEST-SKU en tag `testproduct_verwijder_deze`. De echte artikelen worden tijdens deze test niet bijgewerkt.

## Bestanden
- `app/suppliers/rhodius_sales_unit.py`: centrale exportregel.
- `app/shopify/sync.py`: payload, voorraadconversie en testupload van lokale afbeeldingen.
- `app/web/dashboard.py`: uitleg bij de bestaande testuploadknop.
- `tests/test_rhodius_sales_unit.py`: regressietests.
- `output/rhodius_ve_test_20260918/results.json`: teruggelezen testartikelen.

## Flow
VE = 1 gebruikt GTIN-code; VE > 1 gebruikt GTIN/verpakking. Ontbreekt de oorspronkelijke verpakkingscode, dan mag een al in het PIM opgeslagen, officieel geverifieerde websitecode met dezelfde verpakkingsinhoud worden gebruikt. Nooit stilzwijgend de stukcode gebruiken.

De huidige PIM-verkoopprijs (sale_price, anders price) en kostprijs worden bij €/stuk met VE vermenigvuldigd. Prijzen per pak/blik worden niet nogmaals vermenigvuldigd. Afronden gebeurt na vermenigvuldiging. Gewicht/VE heeft voorrang; anders stukgewicht maal VE. Bronvelden en PIM-prijzen worden niet overschreven. De bestaande stuk-/pakvoorraad wordt op dezelfde basis naar hele verkoopeenheden omgerekend.

Testconcepten zonder GTIN krijgen een lege barcode en het label `controle_gtin_verkoopeenheid`. Reguliere export zonder bijbehorende GTIN wordt geblokkeerd. Alleen prijzen synchroniseren is voor Rhodius geblokkeerd: barcode, prijs en gewicht moeten dezelfde eenheid hebben.

De knop voor testupload blijft in het bestaande bron-/Shopify-koppelingsscherm staan. Testupload gebruikt de bestaande stagingfunctie voor lokale PIM-foto's.

## Testplan
Gerichte pytest-tests voor VE 1/meerdere/samengesteld/ongeldig, ontbrekende GTIN, websiteherkomst, prijsafronding, prijs per pak, gewicht en voorraad. Daarnaast bestaande Shopify- en Certilas-regressietests: 34 tests geslaagd. Alle negen concepten zijn teruggelezen; barcode, verkoopprijs, kostprijs, gewicht en READY-status van afbeeldingen zijn gecontroleerd. Na upload de negen TEST-SKU's, conceptstatus en barcode teruglezen uit Shopify.

## Openstaande punten
Verpakkings-GTIN ontbreekt voor 900385, 900386, 305407 en 305408. Voor 353084 en 305969 is de expliciete website-verpakkingscode uit het PIM gebruikt.

900386 bevat tegenstrijdige gewichten: Gewicht/VE = 3,000 kg, maar 0,019 kg/stuk × VE 20 = 0,380 kg. De expliciete Gewicht/VE is geëxporteerd; broncontrole nodig voordat het echte artikel wordt geëxporteerd.

## Changelog
2026-09-18: VE-regel en negen testconcepten toegevoegd.

## Testartikelen

| Test-SKU | VE | Shopify |
|---|---:|---|
| TEST-207079 | 10 | [Concept](https://weldingshop-nl.myshopify.com/admin/products/10402564243785) |
| TEST-900385 | 12 | [Concept](https://weldingshop-nl.myshopify.com/admin/products/10402564440393) |
| TEST-900383 | 1 | [Concept](https://weldingshop-nl.myshopify.com/admin/products/10402566275401) |
| TEST-900386 | 20 | [Concept](https://weldingshop-nl.myshopify.com/admin/products/10402566472009) |
| TEST-353084 | 10 | [Concept](https://weldingshop-nl.myshopify.com/admin/products/10402566603081) |
| TEST-305407 | 10 | [Concept](https://weldingshop-nl.myshopify.com/admin/products/10402566766921) |
| TEST-305408 | 10 | [Concept](https://weldingshop-nl.myshopify.com/admin/products/10402566963529) |
| TEST-305969 | 10 | [Concept](https://weldingshop-nl.myshopify.com/admin/products/10402567061833) |
| TEST-210632 | 10 | [Concept](https://weldingshop-nl.myshopify.com/admin/products/10402567487817) |
