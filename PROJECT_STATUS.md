# Weldingshop PIM - Project Status

Laatst bijgewerkt: 2026-09-18

Rhodius: beide barcodes blijven scanbaar via afzonderlijke stuk- en VE-eenheden. Negen bronartikelen leveren 17 TEST-concepten met acht native voorraadkoppelingen. Onlinekeuze staat nog open; vier verpakkings-GTIN’s ontbreken. Beide barcodes zijn zoekbaar in het PIM. Zie `docs/RHODIUS_SALES_UNIT.md`.

De melding voor ontbrekende productafbeeldingen noemt geen onjuiste leverancier meer. Zij meldt uitsluitend dat voor het geselecteerde artikel nog geen afbeelding in PIM is opgeslagen.

## Inkoopprijzen

Ultimatron gebruikt `Dealer Price(Excl.VAT)` als netto inkoopprijs. De verwerking van bedragen zoals `222.25€` is hersteld en 17 bestaande prijzen zijn bijgewerkt. ULM-12-200 mist deze bronwaarde. Tab 5 toont de bronkoppeling; handmatige invoer is alleen een optionele afwijking.

Tab 5 heeft directe netto inkoopprijsinvoer per artikel, met prijsbescherming bij bronimport en kortingsregels. Elf relevante tests slagen, inclusief de Streamlit-schermtest. Er zijn geen leveranciersprijzen ingevuld tijdens deze aanpassing.

## Ultimatron

Indeling hersteld: alle 17 artikelen onder Lithiumaccu’s. Nominale spanning uit de spreadsheet: 12,8 V (10), 25,6 V (3), 38,4 V (1), 51,2 V (3). Prijzen behouden; 18 gerichte classificatie- en verrijkingstests slagen.

Herstelrun afgerond: 7 verrijkt, 10 niet exact gevonden, 0 verwerkingsfouten. Vertaling per tekstveld voorkomt het samenvoegen van technische eigenschappen en bewaakt technische waarden op hun oorspronkelijke plek. 23 relevante tests slagen.

Tab 8 verrijkt uitsluitend de 17 geïmporteerde spreadsheetartikelen. De Franse categorie en zoekfunctie leveren 7 exacte matches; 10 SKU’s worden niet door een vergelijkbare variant vervangen. De oude categorierun is gestopt en vijf onbedoelde toevoegingen zijn teruggedraaid, inclusief ULM-12-200. Zie `docs/ULTIMATRON_ENRICHMENT.md`.

## Projectstatus

Weldingshop PIM wordt het centrale PIM-platform voor Weldingshop.

v0.6 AI Database Integratie is klaar.

De volgende sprint is v0.7 Shopify AI Export.

## Centrale omgeving

- Centrale projectmap: `/root/weldingshop-pim`
- Dashboard: `https://pim.weldingshop.nl`
- OpenClaw: `https://ai.weldingshop.nl`

## Infrastructuur gereed

- ✓ Ubuntu 24.04
- ✓ Docker
- ✓ Portainer
- ✓ Nginx
- ✓ Let's Encrypt SSL
- ✓ OpenClaw
- ✓ OpenAI Responses API
- ✓ SQLite
- ✓ SQLAlchemy
- ✓ Streamlit Dashboard
- ✓ `https://pim.weldingshop.nl`
- ✓ Basic Authentication
- ✓ Documentatiestructuur
- ✓ Templates
- ✓ OpenClaw heeft toegang tot `/root/weldingshop-pim`

## Werkafspraken

- Windows Terminal
- Server Shell met actieve `.venv`
- Python Code
- OpenClaw
- GUI-first ontwikkeling
- Services-first architectuur
- Projectregels vastgelegd in `docs/development/PROJECT_CONSTITUTION.md`

## AI-flow

```text
Product
↓
Prompt Builder
↓
OpenAI
↓
JSON Validator
↓
ProductService
↓
Database
```

AI-resultaten worden opgeslagen via:

```text
ProductService.mark_ai_generated()
```

Gebruikte databasevelden:

- `ai_title`
- `html_description`
- `ai_generated`

## Dashboard

Qt is verlaten.

Streamlit is gekozen als dashboardframework.

Dashboard URL:

```text
https://pim.weldingshop.nl
```

Basic Authentication is toegevoegd.

## Server

De serveromgeving bestaat uit Ubuntu 24.04, Docker, Portainer, Nginx, Let's Encrypt SSL, Streamlit en OpenClaw.

Nginx + SSL werkt voor:

```text
pim.weldingshop.nl
```

OpenClaw draait via:

```text
https://ai.weldingshop.nl
```

## OpenClaw taak

OpenClaw is verantwoordelijk voor:

- Documentatie onderhouden
- Templates gebruiken
- Changelog bijwerken
- Projectstatus bijwerken
- Index bijwerken
- Featuredocumentatie maken

## Volgende sprint

v0.7 Shopify AI Export:

- AI-velden gebruiken in Shopify-export.
- Export voorbereiden op `ai_title` en `html_description`.
- Exportstatus en controle toevoegen in de GUI.

## Documentatie-afspraak

Na iedere ontwikkelsessie worden bijgewerkt:

- `PROJECT_STATUS.md`
- `CHANGELOG.md`
- `docs/INDEX.md`
- Relevante documentatie
