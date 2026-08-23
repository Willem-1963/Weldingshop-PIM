# Handoff: Ceweld verplichte verkoopbundels

Datum: 18 augustus 2026

## Hervatcommando

Als de gebruiker zegt **“ga verder met bundels”**, lees dit document en ga
verder met het ontwerpen/implementeren van verplichte Ceweld-verkoopbundels in
de Weldingshop PIM en Shopify-sync.

## Besluit

Ceweld-artikelen met een verplichte bundelafname worden in Shopify verkocht
met de **complete bundel als verkoopbare eenheid**.

- Shopify-aantal 1 = één complete bundel.
- Shopify-aantal 2 = twee complete bundels.
- Losse verpakkingen kunnen niet worden gekocht.
- De Shopify-variantprijs is de totaalprijs van één bundel.
- Voorraad moet in verkoopbare bundels worden bijgehouden.
- Er is hiervoor geen Shopify Cart Validation Function nodig.

Dit model geldt alleen als uitsluitend 1, 2, 3 enzovoort complete bundels
mogen worden gekocht. Als een artikel na een minimum ook losse pakken toestaat,
is alsnog een minimum-quantity-validatie nodig.

## Gewenste variant-metavelden

- `custom.verplichte_bundel` — boolean
- `custom.verpakkingen_per_bundel` — integer
- `custom.kg_per_verpakking` — decimal
- `custom.kg_per_bundel` — decimal
- `custom.prijs_per_verpakking` — money/decimal

De velden horen op variantniveau omdat varianten binnen een product andere
verpakkingsgewichten kunnen hebben.

## Prijs- en voorraadmodel

- Inkoopprijs per verpakking blijft afzonderlijk beschikbaar in de PIM.
- Verkoopprijs per verpakking blijft afzonderlijk beschikbaar in de PIM.
- Bundelinkoopprijs = inkoopprijs per verpakking × verpakkingen per bundel.
- Shopify-bundelprijs = verkoopprijs per verpakking × verpakkingen per bundel.
- Shopify-voorraad = verkoopbare complete bundels, niet losse verpakkingen of kg.
- Een artikel met een nul- of ontbrekende bronprijs mag niet automatisch worden
  gepubliceerd.

## Productpaginamelding

Toon een waarschuwing in dezelfde stijl als de levertijdmelding:

> **Let op:** deze prijs geldt voor één verplichte bundel van X verpakkingen à
> Y kg. Totaalgewicht Z kg. Prijs per verpakking €P.

## Gecontroleerde voorbeelden

### Artikel 52432

- Product: E 12018-Mo 3,2 x 350 mm
- KG Ceweld Unit: 1,9 kg
- KG Ceweld Bundle: 11,4 kg
- Verpakkingen per bundel: 6
- Huidige bronprijs: 0; daarom nog geen geldige verkoopprijs/publicatie.

### Artikel 50620

- Product: CroNiMo Ti 2,0 x 300 mm
- KG Ceweld Unit: 2,4 kg
- KG Ceweld Bundle: 14,4 kg
- Verpakkingen per bundel: 6
- Huidige PIM-verkoopprijs per verpakking: €250,69
- Beoogde Shopify-bundelprijs: €1.504,14
- Huidige PIM berekent nog één verpakking en moet worden aangepast.

## Implementatiestatus 18 augustus 2026

De vaste Certilas→Shopify-sync beheert nu op variantniveau:

- `custom.verplichte_bundel`
- `custom.verpakkingen_per_bundel`
- `custom.kg_per_verpakking`
- `custom.kg_per_bundel`
- `custom.prijs_per_verpakking`
- `custom.minimale_afname`

Bij iedere Certilas-sync wordt de bundelverhouding opnieuw berekend als
`KG Ceweld Bundle / KG Ceweld Unit`. Alleen een geheel getal groter dan één
geldt als verplichte bundel. De Shopify-prijs wordt dan de pakprijs maal het
aantal verpakkingen. Decimale komma's worden ondersteund. Vervallen of
ongeldige bundelwaarden verwijderen automatisch oude bundelvelden en oude
productniveauwaarschuwingen. Het actieve Shopify-thema toont
`custom.minimale_afname` van de geselecteerde variant en wisselt deze mee bij
variantselectie. `custom.verwachte_product_levertijd` blijft ongewijzigd.

Regressietests staan in `tests/test_certilas_bundle_prices.py`.

## Nog uit te voeren (historische lijst)

1. Vaststellen welke Ceweld-regels werkelijk een verplichte bundel zijn; een
   gevuld bronveld mag niet zonder beleidscontrole automatisch als verplicht
   worden geïnterpreteerd.
2. PIM-datamodel/mapping uitbreiden met de bundelvelden.
3. Prijsberekening aanpassen zonder de onderliggende pakprijs kwijt te raken.
4. Shopify-sync de bundelprijs en variantmetavelden laten schrijven.
5. Voorraadconversie naar complete bundels ontwerpen en testen.
6. Productthema uitbreiden met de waarschuwing en prijs per verpakking.
7. Pilot uitvoeren met 50620; 52432 pas gebruiken na een geldige bronprijs.
8. Tests toevoegen voor afronding, nulprijzen, niet-verplichte artikelen,
   variantfamilies en Shopify-orders/retouren.

## Labelprogramma (reeds gebouwd)

De PIM bevat inmiddels een labelmodule met productzoeker, 4×6- en DYMO-labels,
veldvolgorde, tekstgroottes, barcode, `custom.locatie`, locatiebewerking,
benoemde ontwerpen, werkplek/printerprofielen en automatische laatste
instellingen. Dit staat los van de nog te bouwen Ceweld-bundellogica.
