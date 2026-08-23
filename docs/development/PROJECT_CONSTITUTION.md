# AI Product Factory - Project Constitution

Dit document bevat de officiële ontwikkelprincipes en projectregels voor AI Product Factory.

## 1. GUI First

Alle functionaliteit moet uiteindelijk beschikbaar zijn via de webinterface.

De webinterface is leidend voor dagelijks gebruik. CLI-tools mogen bestaan voor beheer, test en diagnose, maar zijn niet de primaire gebruikerservaring.

## 2. Service First

Alle logica loopt via Services.

GUI en CLI mogen nooit rechtstreeks de database benaderen. Database-acties lopen via Services en Repositories.

## 3. JSON First

AI levert uitsluitend gevalideerde JSON.

Vrije tekst uit AI wordt niet als bron van waarheid gebruikt. AI-output wordt eerst gevalideerd voordat deze verder wordt verwerkt.

## 4. AI Safety

AI schrijft nooit rechtstreeks naar de database.

De vaste flow is:

```text
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

Alle AI-resultaten lopen via `ProductService` voordat ze worden opgeslagen.

## 5. Documentation First

Na iedere ontwikkelsessie worden bijgewerkt:

- `PROJECT_STATUS.md`
- `CHANGELOG.md`
- `docs/INDEX.md`
- Relevante documentatie

Documentatie is onderdeel van de oplevering.

## 6. Template First

Nieuwe documentatie wordt altijd gemaakt vanuit een template.

De standaardtemplates staan in:

```text
templates/
```

## 7. Feature Documentation

Iedere nieuwe feature krijgt een eigen document.

Featuredocumentatie legt minimaal doel, status, bestanden, flow, testplan, openstaande punten en changelog vast.

## 8. Test First

Nieuwe functionaliteit wordt getest voordat deze als gereed wordt beschouwd.

Een feature is pas klaar als de relevante handmatige of automatische tests zijn uitgevoerd en de uitkomst is vastgelegd.

## 9. Security First

Nieuwe dashboards zijn standaard niet publiek toegankelijk.

Authenticatie en beveiliging worden direct meegenomen bij ontwerp en implementatie.

## 10. Architecture Above Speed

Architectuur en onderhoudbaarheid gaan vóór snelle oplossingen.

Snelle oplossingen zijn alleen acceptabel wanneer ze de bestaande architectuur niet verzwakken of expliciet als tijdelijke maatregel zijn gedocumenteerd.

## OpenClaw taak

OpenClaw is verantwoordelijk voor:

- Documentatie onderhouden
- Templates gebruiken
- Changelog bijwerken
- Projectstatus bijwerken
- Index bijwerken
- Featuredocumentatie maken
