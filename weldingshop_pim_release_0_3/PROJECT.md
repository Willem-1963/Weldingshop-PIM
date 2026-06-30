# Weldingshop PIM

**Product Information & Automation Platform**

## Doel
Weldingshop PIM is het centrale systeem voor productbeheer, AI-verrijking en Shopify-synchronisatie.

De SQLite database is de centrale waarheid. Leveranciersbestanden, websites en API's zijn bronnen. Shopify is een uitvoerkanaal.

## Engines

1. Core Engine
   - Configuratie
   - Database
   - Logging
   - Basisinstellingen

2. Import Engine
   - Excel-import
   - CSV-import
   - SKU-matching
   - Normalisatie

3. Quality Engine
   - Ontbrekende data detecteren
   - Kwaliteitsscore
   - Rapportage

4. AI Engine
   - Producttitels
   - HTML-beschrijvingen
   - SEO
   - Tags

5. Shopify Engine
   - Producten aanmaken
   - Producten bijwerken
   - Afbeeldingen
   - Metafields

6. GUI Engine
   - Dashboard
   - Productviewer
   - Taakcentrum
   - Instellingen

## Ontwikkelregels

- De database is de waarheid.
- Producten worden standaard als concept aangemaakt.
- Inventory policy staat standaard op continue.
- Geen product verwijderen zonder bevestiging.
- Elke release moet testbaar zijn.
- Elke stabiele stap wordt gecommit en gepusht.
- Grote leveranciersbestanden en databases horen niet in Git.

## Roadmap

- 0.1 Basisproject, database en import
- 0.2 AI Engine basis
- 0.3 Product Viewer
- 0.4 Echte OpenAI-integratie
- 0.5 Shopify API
- 0.6 GUI basis
- 1.0 Windows installer
