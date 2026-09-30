# Shopify-boomlaag behouden voor alle leveranciers

Normale leveranciersruns beheren geen boomlaag op bestaande Shopify-producten.
Tags, Shopify `category` en deze velden blijven merchant-managed:

- `custom.productgroep`, `custom.productsoort`, `custom.uitvoering`, `custom.filter`
- `custom.subcategorie_3`, `custom.subcategorie_4`, `custom.subcategorie_5`
- `custom.aandrijving`, `custom.model`, `custom.capaciteit`

Dit geldt voor elke huidige en toekomstige leverancier, ook met oude
PIM-categorie-locks/imports en wanneer Shopify- of PIM-boomwaarden leeg zijn.
Een expliciet goedgekeurde boomimport is een afzonderlijke beheeractie.

De sync laat tags/category en volledige metafield-lijsten weg uit bestaande
`productSet`-updates. Commerciële product- en variantmetavelden worden apart
bijgewerkt met `metafieldsSet`. Dit behoudt niet-meegestuurde Shopify-velden.
Familieverpakkingsinformatie en Certilas-bundelregels blijven werken.

Automatische cleanup verwijdert geen boomvelden; ook de voormalige Certilas
`custom.filter`-cleanup is uitgeschakeld. Een extra controle vóór bulk-upload
weigert onveilige vervangingen en schrijfacties/verwijderingen van boomvelden.

Product Maker en afzonderlijk opslaan van leveranciersproducten behouden
dezelfde scheiding tussen structuur en commerciële velden. Nieuwe producten
behouden hun bestaande initiële import. Productstatus, publicatie, voorraadroute
en het toegestane verwijderen van uit-assortimentproducten blijven ongewijzigd.

De VP-scheduler blijft uitgeschakeld. Deze wijziging start geen leveranciersrun.
