# Direct labels afdrukken via Windows

Labels > Mobiel bevat een gekoppelde Windows-printservice voor de USB-printer
`gprinter gp-1324d`. Het installatiepakket wordt in de ingelogde PIM gegenereerd.
De Windows-pc haalt iedere vijf seconden opdrachten via HTTPS op. Er zijn geen
inkomende verbindingen naar het winkelnetwerk nodig. De gebruiker moet bij
Windows aangemeld blijven; de agent start via een Startup-snelkoppeling.

Het bestaande HTML-label wordt door Playwright op 203 dpi gerenderd. Windows
System.Drawing.Printing drukt de PNG af op 4x6 inch, liggend. De werkelijke
PageBounds worden gecontroleerd om afdrukken op een verkeerd papierformaat te
voorkomen. `submitted` betekent aangeboden aan de spooler, niet fysiek gedrukt.

## Serverconfiguratie

- De PIM en `weldingshop-pim-webhook.service` draaien als dezelfde gebruiker.
- Installeer de Playwright-browser onder die gebruiker:
  `sudo -u ubuntu .venv/bin/playwright install chromium --only-shell`.
- Wachtrij en credential staan in `data/database/label_print.sqlite` (geen Git).
- De bestaande webhookserver behandelt uitsluitend POST `/label-print/claim`
  en `/label-print/complete`, geauthenticeerd met het printertoken. Deze API kan
  geen printopdrachten toevoegen; dat kan alleen de ingelogde PIM.
- Nginx stuurt `/label-print/` door naar `127.0.0.1:8502`, met `auth_basic off`,
  behoud van de Authorization-header en een bodylimiet van 4k. De PIM-login
  blijft voor alle andere PIM-pagina's verplicht.

De installatie-ZIP bevat het token; behandel hem als een credential. Windows
beperkt toegang tot de installatiemap tot de gebruiker en SYSTEM. Stop en
verwijder de agent om de lokale installatie uit te schakelen. Bij verlies van
het pakket moet de beheerder het `bridge.token` vervangen, een nieuw pakket
downloaden en de agent opnieuw installeren/starten.

## Betrouwbaarheid

Een printklik heeft een idempotentiesleutel. Claim gebeurt in een SQLite-
schrijftransactie. Claimed jobs worden nooit automatisch opnieuw aangeboden,
ook niet bij een crash of verbindingsverlies. De agent bewaart een lokale
status voordat hij afdrukt en herhaalt alleen de terugmelding. Een crash
tijdens afdrukken leidt tot `uncertain`; eerst de printer en spooler controleren.
Niet opgehaalde opdrachten verlopen na tien minuten. De agent verwerkt verlopen
opdrachten niet. Oude jobs worden na dertig dagen opgeruimd bij een poll.

Tests: `pytest tests/test_label_print.py tests/test_free_labels.py`.
Een fysieke Windows/USB-proefafdruk blijft nodig na installatie bij de gebruiker.
