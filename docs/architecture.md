# Weldingshop PIM - Technisch Ontwerp v1.0

## Doel
Een centraal Product Information Management systeem voor Weldingshop.

Niet alleen voor SP Tools, maar later ook voor:
- Telwin
- GCE
- Jasic
- ESAB
- RAVAS
- Sherman
- Valkenpower

## Hoofdprincipe
De SQLite database is de waarheid.

Bronnen:
- Excel
- CSV
- Website
- API

Uitvoer:
- Shopify
- CSV
- API
- Rapportages

## Engines

### 1. Core Engine
Verantwoordelijk voor:
- Configuratie
- Database
- Logging
- Foutafhandeling
- Basisinstellingen

### 2. Import Engine
Verantwoordelijk voor:
- Excel import
- CSV import
- SKU matching
- Brondata normaliseren

### 3. Quality Engine
Controleert:
- Ontbrekende EAN
- Ontbrekende prijs
- Ontbrekende beschrijving
- Ontbrekende afbeeldingen
- Ontbrekend gewicht
- Ontbrekende categorie
- Dubbele gegevens

### 4. Website Engine
Verantwoordelijk voor:
- Productpagina ophalen
- Afbeeldingen verzamelen
- Productdetails verzamelen
- Specificaties verzamelen
- Downloads verzamelen

### 5. AI Engine
Verantwoordelijk voor:
- Nederlandse HTML beschrijvingen
- SEO titel
- SEO beschrijving
- Tags
- Producttitel verbetering

### 6. Shopify Engine
Verantwoordelijk voor:
- Producten aanmaken
- Producten bijwerken
- Afbeeldingen synchroniseren
- Metafields vullen
- Status concept/publicatie beheren

## Database tabellen

### products
Hoofdproductdata.

### product_images
Alle afbeeldingen per SKU.

### product_specifications
Technische specificaties per SKU.

### quality_reports
Kwaliteitsscore en ontbrekende velden per SKU.

### import_runs
Logboek van importacties.

### shopify_sync
Shopify product ID, variant ID en synchronisatiestatus.

## Workflow

1. Leveranciersbestand importeren.
2. Data normaliseren.
3. Producten opslaan in database.
4. Quality Engine uitvoeren.
5. Website Engine vult ontbrekende gegevens aan.
6. AI Engine maakt HTML en SEO.
7. Shopify Engine synchroniseert gewijzigde producten.

## Veiligheidsregels

- Producten standaard als concept.
- Inventory policy standaard continue.
- Geen product verwijderen zonder handmatige bevestiging.
- Eerst testen in kleine batches.
- Elke werkende stap committen in Git.
