# VP Shopify-velden bij synchronisatie

Deze bescherming is inmiddels veralgemeniseerd naar alle leveranciers;
zie [Shopify-boomlaag behouden](SHOPIFY_TREE_PROTECTION.md).

Voor bestaande VP-producten beheert Shopify de boomlaag: tags, `category` en
de custom-metavelden `productgroep`, `productsoort`, `uitvoering`, `filter`,
`subcategorie_3`, `subcategorie_4`, `subcategorie_5`, `aandrijving`, `model`
en `capaciteit`.

De productsync mag titel, omschrijving, media, prijzen en andere commerciële
velden bijwerken. Metavelden in `pim.*` en niet-boomvelden zoals
`custom.voorraad_tabel` en de delivery notice mogen worden bijgewerkt wanneer
de bestaande bron/mapping ze aanlevert. Deze beleidswijziging introduceert geen
nieuwe voorraadtabellen of voorraadroute. VP gebruikt momenteel Shopify als
voorraadbron; voorraadaantallen worden niet door de VP-productsync overschreven.

Op bestaande VP-producten worden metafield-lijsten volledig weggelaten uit
`productSet`. Toegestane velden worden apart met `metafieldsSet` bijgewerkt,
zodat andere bestaande metavelden niet door lijstvervanging worden verwijderd.
De automatische taxonomy-cleanup en aanvullende-productensync worden voor VP
overgeslagen.

Een bestaande `category_file_import` met `locked: true` geeft de normale sync
geen toestemming om de Shopify-boomlaag te wijzigen. Een boomimport moet
afzonderlijk en expliciet voor die actie worden goedgekeurd. Het historische
importscript vereist daarom `--approve-tree-import` voor prepare/apply.
Een lege of ontbrekende PIM-/lockwaarde veroorzaakt tijdens normale sync geen
verwijdering. Product Maker beschermt bestaande `VP-`-SKU's op dezelfde manier.

Nieuwe producten behouden de bestaande initiële import van velden.

De aparte `valkenpower-official-category` timer en service zijn uitgeschakeld.
Het bijbehorende backfill-script vereist ook expliciete
`--approve-tree-import` goedkeuring. De PIM- en voorraad-webhookdiensten zijn
herstart om de bijgewerkte bescherming te laden. De automatische VP-planning
blijft uitgeschakeld.
