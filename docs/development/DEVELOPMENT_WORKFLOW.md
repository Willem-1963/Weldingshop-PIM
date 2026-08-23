# Development Workflow

## Principe

Weldingshop PIM wordt GUI-first en service-first ontwikkeld.

Nieuwe functionaliteit wordt pas als gereed beschouwd wanneer implementatie, test en documentatie samen kloppen.

## Standaard workflow

Iedere feature doorloopt deze stappen:

1. Ontwerp
2. Implementatie
3. Test
4. Documentatie
5. `PROJECT_STATUS.md`
6. `CHANGELOG.md`
7. `docs/INDEX.md`

## OpenClaw controle

Na iedere afgeronde feature controleert OpenClaw automatisch:

- ✓ `PROJECT_STATUS.md`
- ✓ `CHANGELOG.md`
- ✓ `docs/INDEX.md`
- ✓ Juiste template gebruikt
- ✓ Documentatie compleet

## OpenClaw taak

OpenClaw is verantwoordelijk voor:

- Documentatie onderhouden
- Templates gebruiken
- Changelog bijwerken
- Projectstatus bijwerken
- Index bijwerken
- Featuredocumentatie maken

## Werkafspraken

- Windows Terminal
- Server Shell met actieve `.venv`
- Python Code
- OpenClaw

## Projectmap

Werk in:

```text
/root/weldingshop-pim
```

## Belangrijke keuzes

- Qt is verlaten.
- Streamlit is gekozen.
- Nginx + SSL werkt voor `pim.weldingshop.nl`.
- Basic Authentication is toegevoegd.

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
