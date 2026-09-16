# Ultimatron-catalogusverrijking

## Doel

Lithiumaccu’s ontdekken op de officiële categoriepagina, productpagina’s volgen en volledige Nederlandse productinformatie en galerijfoto’s in de leveranciers-PIM opslaan.

## Status

Ingesteld en getest op 16 september 2026. De categorie bevat 18 vermeldingen en 17 unieke SKU’s. Proefimport ULM-12-200: 9 afbeeldingen, 40 specificaties, EAN 9331416835964, conceptstatus.

## Bestanden

- `app/suppliers/ultimatron.py`: categorie, extractie, vertaling, validatie, leveranciersopslag en achtergrondtaak.
- `app/suppliers/on_demand_import.py`: route voor een afzonderlijk artikel.
- `app/web/dashboard.py`: startknop en voortgang in tab 8.
- `tests/test_ultimatron.py`: artikelidentiteit, media, dubbele velden, technische waarden, prijs/voorraad en vergrendeling.

## Flow

Selecteer Ultimatron → Bron & import → 8. Verrijkingsregels → Ultimatron-catalogus ophalen en verrijken. Volg de voortgang met Voortgang vernieuwen. De taak draait op de achtergrond; een procesvergrendeling voorkomt gelijktijdige catalogusruns. Een afzonderlijk artikel kan via Productviewer worden verwerkt.

Alleen officiële productlinks uit de lithiumcategorie worden gevolgd. Dubbele SKU’s worden eenmaal verwerkt. Identiteit wordt gecontroleerd in de productsamenvatting. Alleen de eigen galerij levert foto’s. Franse brongegevens worden bewaard; Nederlandse tekst wordt gevalideerd voordat de leveranciersservice opslaat. Getallen/codes zijn tijdens vertaling vervangen door beschermde markeringen. Prijzen/voorraad worden niet bijgewerkt; handmatig vergrendelde producten worden overgeslagen. Nieuwe producten blijven concept in PIM. Shopify-publicatie is een aparte actie.

## Testplan

`PYTHONPYCACHEPREFIX=/tmp/ultimatron-pycache .venv/bin/python -m pytest tests/test_ultimatron.py tests/test_website_enrichment_versions.py -q -p no:cacheprovider`

Live proef met ULM-12-200 uitgevoerd. Controleer Nederlandse titel/omschrijving, specificaties, EAN en negen afbeeldingen in Producten/Productviewer.

## Openstaande punten

- De overige 16 unieke artikelen zijn nog niet geïmporteerd; de gebruiker start de categorie via tab 8.
- Afbeeldingen blijven de originele bronafbeeldingen; eventuele ingebakken Franse tekst wordt niet vertaald.
- PDF-handleidingen worden niet vertaald in deze import.
- Websitewijzigingen en individuele vertaalfouten worden als fouten bij de taak getoond.

## Changelog

- 2026-09-16: officiële categorie-import, Nederlandse vertaling, validatie en startknop toegevoegd.
