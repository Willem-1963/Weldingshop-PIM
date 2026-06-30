# Release 0.2 - AI Engine basis

Deze release voegt de basisstructuur voor de AI Engine toe.

## Nieuwe onderdelen

- app/ai/providers/base.py
- app/ai/providers/openai_provider.py
- app/ai/generators/title_generator.py
- app/ai/generators/description_generator.py
- app/ai/generators/seo_generator.py
- app/ai/ai_engine.py
- app/core/config.py
- config/settings.example.env

## Testcommando

```powershell
py -m app.ai.ai_engine
py -m app.core.dashboard
```

## Verwacht resultaat

De AI Engine verrijkt de eerste 10 producten lokaal met een voorlopige titel en HTML-beschrijving. Er wordt nog geen externe OpenAI API aangeroepen.
