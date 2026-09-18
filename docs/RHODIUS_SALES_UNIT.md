# Rhodius: stukbarcode, verpakkingsbarcode en VE

## Doel
Een losse schijf en een doos moeten beide scanbaar zijn. De stukbarcode betekent één stuk; de doosbarcode betekent het aantal stuks in VE. De keuze om online stukken, verpakkingen of beide te verkopen staat hier los van en wordt later gemaakt.

## Status
Geïmplementeerd op 2026-09-18. De negen geselecteerde PIM-artikelen hebben samen 17 afzonderlijke Shopify-testconcepten: negen stukartikelen en acht VE-verpakkingen. Bij VE = 1 is maar één artikel nodig. De acht verpakkingen zijn via native Shopify-componentrelaties gekoppeld aan hun stukartikel. Alle artikelen blijven DRAFT, met TEST-SKU en tag `testproduct_verwijder_deze`.

## Bestanden
- `app/suppliers/rhodius_sales_unit.py`: beide barcodes, eenheden, prijzen en gewichten.
- `app/shopify/rhodius_test_units.py`: concepten, native voorraadrelatie en PIM-registratie.
- `app/shopify/sync.py`: testupload en gedeelde exportlogica.
- `app/suppliers/hub.py`: zoeken op beide GTIN-velden en opgeslagen website-verpakkingscode.
- `app/web/dashboard.py`: overzicht van beide scaneenheden en links bij productdetails en productviewer.
- `tests/test_rhodius_sales_unit.py`, `tests/test_rhodius_test_units.py`, `tests/test_rhodius_barcode_search.py`: regressietests.
- `output/rhodius_scan_units_20260918/`: databasebackup, rapporten en teruggelezen Shopify-resultaten.

## Flow
1. Lees de originele stuk-GTIN, verpakking-GTIN en VE uit het PIM. Brongegevens blijven behouden.
2. Maak het stukartikel met één stuk per scan. Bij VE > 1 krijgt dit testartikel de SKU `TEST-{sku}-STUK`; de bestaande `TEST-{sku}` blijft de verpakking. Bij VE = 1 blijft `TEST-{sku}` het enige artikel.
3. Maak bij VE > 1 een aparte verpakking met de verpakkings-GTIN. Als deze ontbreekt in de import, mag de expliciete verpakkingscode uit eerder geverifieerde officiële websitegegevens worden gebruikt, mits de inhoud gelijk is aan VE.
4. Koppel verpakking aan VE × stukvariant met `productVariantRelationshipBulkUpdate`. Shopify beheert de gedeelde voorraad via deze native relatie; er wordt geen tweede zelfstandige fysieke voorraad voor de doos aangemaakt.
5. Sla testeenheden en relaties op in de leveranciersdatabase, tabel `rhodius_test_sales_units`. Onlinebeleid blijft `undecided`. Geen publicatiekeuze wordt afgeleid uit VE. Nieuwe Rhodius-eenheden worden niet automatisch online geactiveerd zolang de onlinekeuze openstaat.
6. Toon beide barcodes en stuks per scan in het PIM. Beide zoekschermen doorzoeken de eigen GTIN-velden, zonder GTINs van andere artikelen uit catalogusverrijking mee te nemen.

Afzonderlijke Shopify-producten maken een latere kanaalkeuze per eenheid mogelijk. Een generieke tweede barcode op dezelfde variant zou hetzelfde aantal afboeken en voldoet daarom niet.

### Prijzen en gewicht
De PIM-verkoopprijs is `sale_price`, anders `price`. Bij €/stuk is de verpakking de bronprijs maal VE; bij €/pak of €/blik wordt de stukprijs afgeleid door delen door VE. Dezelfde bronbasis wordt gebruikt voor kostprijs. Afronden gebeurt na omrekening. De verpakkingsprijs wordt bij het koppelen expliciet als FIXED meegegeven; Shopify mag niet opnieuw afgeronde stukprijzen optellen.

Het stukartikel gebruikt stukgewicht; de verpakking gebruikt Gewicht/VE, anders stukgewicht maal VE. Voorraad wordt bij export naar dezelfde eenheid omgerekend. De PIM-bronprijzen en bronvoorraad blijven behouden. Testuploads zetten geen fictieve voorraad en voeren geen verkooporders uit.

### Ontbrekende of dubbel gebruikte barcode
Een ontbrekende verpakkingscode blokkeert het stukartikel niet. Alleen het verpakkingsconcept krijgt een lege barcode en `controle_gtin_verkoopeenheid`. Reguliere export van een eenheid zonder de bijbehorende barcode wordt geblokkeerd. Bij VE > 1 mogen stuk en verpakking niet dezelfde barcode krijgen; zo'n verpakking wordt eveneens gemarkeerd voor controle.

## Testplan
45 gerichte tests slagen, inclusief regressies voor prijsafronding, dezelfde barcode bij VE > 1, ontbrekende verpakkings-GTIN, een prijsbron per pak, voorraadconversie, dubbele uploads, Shopify-fouten, beide zoekschermen en nog niet gekozen onlinebeleid. GraphQL-operaties zijn tegen het Shopify-schema gevalideerd.

Alle 17 concepten worden teruggelezen op barcode, prijs, kostprijs, afbeeldingen en conceptstatus; de acht native voorraadrelaties worden op component-ID en aantal gecontroleerd. Herhaalde upload van 210632 behoudt dezelfde product- en variant-ID's. Een fysieke scan op Shopify POS en een echte winkelverkoop zijn niet uitgevoerd; de producten zijn nog testconcepten.

## Openstaande punten
- Verpakkings-GTIN ontbreekt voor 900385, 900386, 305407 en 305408. De stukbarcodes zijn wel aanwezig.
- Voor 353084 en 305969 is de officiële website-verpakkingscode uit het PIM gebruikt.
- 900386 bevat tegenstrijdige gewichten: Gewicht/VE = 3,000 kg, maar 0,019 kg/stuk × VE 20 = 0,380 kg. Beide originele waarden blijven zichtbaar; broncontrole blijft nodig.
- De kanaalkeuze (winkel/website, stuk/VE) volgt later. Er zijn nu geen echte artikelen gewijzigd of verkoopkanalen geactiveerd.

## Changelog
2026-09-18: stuk en verpakking als afzonderlijke scaneenheden met native gedeelde voorraad. Eerdere interpretatie dat VE uitsluitend de verkoopmogelijkheid bepaalde, vervangen.

## Bronnen
- [Shopify variant fixed bundles](https://shopify.dev/docs/apps/build/product-merchandising/bundles/add-variant-fixed-bundle)
- [Shopify bundleprijs](https://shopify.dev/docs/api/admin-graphql/latest/input-objects/PriceInput)
- [Shopify verkoopkanalen voor bundels](https://help.shopify.com/en/manual/products/bundles)

## Testartikelen

| PIM-SKU | VE | Stukartikel | Verpakking |
|---|---:|---|---|
| 210632 | 10 | [4011890101810](https://weldingshop-nl.myshopify.com/admin/products/10402665136457) | [4011890101841](https://weldingshop-nl.myshopify.com/admin/products/10402567487817) |
| 207079 | 10 | [4011890059975](https://weldingshop-nl.myshopify.com/admin/products/10402673000777) | [4011890060407](https://weldingshop-nl.myshopify.com/admin/products/10402564243785) |
| 900385 | 12 | [4011890061909](https://weldingshop-nl.myshopify.com/admin/products/10402673688905) | [GTIN ontbreekt](https://weldingshop-nl.myshopify.com/admin/products/10402564440393) |
| 900383 | 1 | [4011890061916](https://weldingshop-nl.myshopify.com/admin/products/10402566275401) | Zelfde eenheid |
| 900386 | 20 | [4011890061923](https://weldingshop-nl.myshopify.com/admin/products/10402674311497) | [GTIN ontbreekt](https://weldingshop-nl.myshopify.com/admin/products/10402566472009) |
| 353084 | 10 | [4011890074831](https://weldingshop-nl.myshopify.com/admin/products/10402674671945) | [4011890114292](https://weldingshop-nl.myshopify.com/admin/products/10402566603081) |
| 305407 | 10 | [4011890079287](https://weldingshop-nl.myshopify.com/admin/products/10402674934089) | [GTIN ontbreekt](https://weldingshop-nl.myshopify.com/admin/products/10402566766921) |
| 305408 | 10 | [4011890079294](https://weldingshop-nl.myshopify.com/admin/products/10402675327305) | [GTIN ontbreekt](https://weldingshop-nl.myshopify.com/admin/products/10402566963529) |
| 305969 | 10 | [4011890096703](https://weldingshop-nl.myshopify.com/admin/products/10402675687753) | [4011890116494](https://weldingshop-nl.myshopify.com/admin/products/10402567061833) |
