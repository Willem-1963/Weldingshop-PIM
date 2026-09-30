# Voorraad behouden bij synchronisatie

## Doel
Shopify-voorraad behouden met de bestaande voorraadbron in tab 4.

## Status
Extra vinkje op verzoek verwijderd. Bestaande voorraadbron behouden.

## Bestanden
`app/web/dashboard.py`, `app/suppliers/hub.py`, `app/shopify/sync.py`, `tests/test_inventory_source.py`.

## Flow
Kies ‘Voorraad uit Shopify gebruiken’ en sla de voorraadregel op. De voorraad van het gekozen Shopify-magazijn bepaalt de beschikbaarheid en wordt niet overschreven tijdens synchronisatie.

## Testplan
Bestaande tests voor de Shopify-voorraadbron en opslag van instellingen; syntaxcontrole van de gewijzigde modules.

## Openstaande punten
Geen.

## Changelog
2026-09-20: extra vinkje toegevoegd en daarna op verzoek verwijderd omdat de bestaande Shopify-voorraadbron al voorraadbehoud biedt.
