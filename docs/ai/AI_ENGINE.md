# AI Engine

## Doel

De AI Engine verrijkt productdata met AI-gegenereerde titel en HTML-omschrijving.

De output wordt alleen gebruikt wanneer deze als JSON gevalideerd is.

## Complete AI-flow

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

## Verwerking

1. Product ophalen.
2. Prompt bouwen.
3. OpenAI Responses API aanroepen.
4. JSON-response valideren.
5. Resultaat opslaan via `ProductService.mark_ai_generated()`.

## Veiligheidsregels

- AI schrijft nooit rechtstreeks naar de database.
- Vrije AI-tekst wordt niet als bron van waarheid gebruikt.
- Alleen gevalideerde JSON mag naar `ProductService`.
- GUI en CLI gebruiken Services en benaderen de database niet rechtstreeks.

## Output

De gevalideerde AI-output vult:

- `ai_title`
- `html_description`
- `ai_generated`

## Status

v0.6 AI Database Integratie is klaar.

De AI-output is klaar om in v0.7 door Shopify AI Export gebruikt te worden.
