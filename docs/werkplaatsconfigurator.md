# Werkplaatsconfigurator in de PIM

De PIM-module **Werkplaatsconfigurator** werkt volledig zelfstandig op de PIM-server.
OpenClaw is niet nodig voor de pagina, downloads, handmatige updates of maandplanning.

## Tabs

- **Overzicht** toont de actuele productieversie en alle live categorieën met aantallen.
- **Modellen** toont live modellen en exporteert XLSX, CSV en JSON. Eén model is ook
  afzonderlijk als de oorspronkelijke catalogus-JSON te downloaden.
- **Updaten** vergelijkt Shopify, de live configurator en Valkenpower. Productnaam, SKU,
  prijs, beschikbaarheid, afbeelding en URL van bestaande modellen worden veilig
  gesynchroniseerd. Verdwenen modellen worden alleen via een themapreview verwijderd.
- **Historie** registreert productieversies en iedere handmatige of automatische update,
  inclusief Shopify- en regressiestatus.

## Veilige Shopify-update

1. De PIM leest de live catalogus en Shopify-producten.
2. Nieuwe producten worden uitsluitend toegevoegd als het productmetaveld
   `pim.workplace_configurator_model` complete JSON bevat met een bestaand modeltype,
   categorie en positieve buitenmaten. Onvolledige producten krijgen **Actie vereist**.
3. Bevindingen worden in Shopify vastgelegd in `pim.workplace_configurator_sync`.
4. Bij cataloguswijzigingen dupliceert de PIM het actuele MAIN-thema.
5. De nieuwe catalogus gaat alleen naar de ongepubliceerde kopie.
6. Statische validatie, Shopify-koppeling, preview-HTTP, cataloguschecksum, 3D-canvas,
   interface en browserfouten vormen samen de regressietest.
7. Alleen als alles slaagt en MAIN tijdens de run niet is gewijzigd, publiceert de PIM
   de preview. Bij fouten blijft het bestaande live-thema actief.

## Planning

Systemd-timer `weldingshop-workplace-configurator-update.timer` start de update op de
**eerste maandag van iedere maand om 12:00 uur, Europe/Amsterdam**. De knop **Updaten**
start dezelfde procedure handmatig. Beide startwijzen zijn zichtbaar in Historie.

## Opslag

De module gebruikt `data/workplace_configurator.sqlite3` voor versies, modelsnapshots,
updateruns, bevindingen en Valkenpower-snapshots. Een bestandslock voorkomt gelijktijdige
automatische updates.
