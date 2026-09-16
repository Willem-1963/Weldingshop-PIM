# Ultimatron-verrijking van spreadsheetartikelen

## Doel

De geïmporteerde spreadsheet bepaalt de artikelcatalogus. De officiële website levert aanvullende Nederlandse teksten, specificaties, EAN en galerijfoto’s voor exact dezelfde SKU’s.

## Status

De herstelrun is afgerond: 17 gecontroleerd, 7 verrijkt met Nederlandse inhoud en foto's, 10 zonder exacte website-overeenkomst, 0 verwerkingsfouten. Vertaling gebeurt per tekstveld in begrensde batches met behoud van alle veld-ID’s, alinea’s en technische markeringen. Gelijk vertaalde specificatielabels blijven aparte eigenschappen.

De selectie is op 16 september 2026 gecorrigeerd: alleen de 17 artikelen met een bronartikelnummer uit de spreadsheet worden verwerkt. Vijf onbedoeld door de eerdere categorierun toegevoegde artikelen zijn na een herstelkopie verwijderd, inclusief ULM-12-200. De officiële Franse categorie en zoekfunctie leveren voor 7 spreadsheet-SKU’s een exacte pagina; 10 SKU’s hebben daar geen exacte match.

## Bestanden

- `app/suppliers/ultimatron.py`: spreadsheetselectie, websiteonderzoek, extractie, vertaling, validatie, opslag en achtergrondtaak.
- `app/suppliers/on_demand_import.py`: route voor een afzonderlijk artikel.
- `app/web/dashboard.py`: startknop en voortgang in tab 8.
- `tests/test_ultimatron.py`: identiteit, media, dubbele velden, technische waarden, prijs/voorraad, vergrendeling en catalogusafbakening.

## Flow

Productgroep: `Lithiumaccu’s`. Het spanningsfilter gebruikt uitsluitend `Nominal Voltage` uit de spreadsheet, genormaliseerd als `Nominale spanning: 12,8 V`. Spanningen uit beschrijvingen, serieaansluitingen en laadspecificaties worden niet als nominale spanning gebruikt. Alleen als het spreadsheetveld ontbreekt mag het exact benoemde nominale specificatieveld van de officiële pagina worden gebruikt. Import, verrijking en filterverversing gebruiken dezelfde classificatiefunctie in `app/suppliers/ultimatron_classification.py`.

Selecteer Ultimatron → Bron & import → 8. Verrijkingsregels → Ultimatron-spreadsheetartikelen verrijken. Volg de voortgang met Voortgang vernieuwen. De taak verwerkt bestaande PIM-artikelen waarvan het bronartikelnummer overeenkomt met de gekoppelde spreadsheetkolom. De categorie vormt alleen een URL-index. Ontbrekende SKU’s worden exact gezocht via de officiële zoekfunctie. Een niet gevonden SKU blijft ongewijzigd in PIM; een vergelijkbare variant wordt niet gebruikt.

Een procesvergrendeling voorkomt gelijktijdige runs. De catalogusroute mag geen producten aanmaken: zowel vooraf als direct voor opslag wordt gecontroleerd of de SKU nog bestaat. Zo wordt een tijdens de taak verwijderd artikel niet opnieuw aangemaakt. Prijzen, voorraad en handmatig vergrendelde inhoud blijven behouden.

Identiteit wordt gecontroleerd in de productsamenvatting. Alleen de eigen galerij levert foto’s. Franse brongegevens worden bewaard; Nederlandse tekst wordt gevalideerd voordat de leveranciersservice opslaat. Getallen/codes zijn tijdens vertaling beschermd met markeringen. Shopify-publicatie is een aparte actie.

## Testplan

`PYTHONPYCACHEPREFIX=/tmp/ultimatron-pycache .venv/bin/python -m pytest tests/test_ultimatron.py -q -p no:cacheprovider`

Controleer dat de catalogus geen websiteartikelen toevoegt, verwijderde SKU’s niet opnieuw aanmaakt, bronprijzen behoudt en niet gevonden SKU’s afzonderlijk rapporteert.

## Openstaande punten

- Voor 10 spreadsheetartikelen is geen exacte pagina gevonden op de officiële Franse website; de brongegevens blijven beschikbaar.
- Afbeeldingen blijven de originele bronafbeeldingen; ingebakken Franse tekst wordt niet vertaald.
- PDF-handleidingen worden niet vertaald in deze import.
- Websitewijzigingen en individuele vertaalfouten worden apart gemeld.

## Changelog

- 2026-09-16: productgroep en nominale spanningsfilters voor alle 17 spreadsheetartikelen hersteld: 12,8 V (10), 25,6 V (3), 38,4 V (1), 51,2 V (3). Zes classificatietests en twaalf verrijkingstests slagen.

- 2026-09-16: categorie-import toegevoegd, vervolgens gecorrigeerd naar uitsluitend spreadsheetartikelen; verkeerde categorierun gestopt en onbedoelde toevoegingen teruggedraaid.
- 2026-09-16: leverancierneutrale melding voor ontbrekende afbeeldingen.
