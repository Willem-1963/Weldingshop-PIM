# Module AI Core

Doel: een stabiele basis voor AI-verrijking zonder direct afhankelijk te zijn van een specifieke AI-provider.

## Onderdelen

- PromptBuilder
- AIResponseValidator
- AI Core CLI

## Testen

```powershell
py -m app.ai.ai_core_cli 193GE-10
```

De output is een volledige prompt met productdata en het verplichte JSON-outputschema.

## Volgende stap

OpenAI Provider koppelen en gevalideerde JSON opslaan in de database.
