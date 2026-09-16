# Handmatige inkoopprijzen

## Doel

Een netto inkoopprijs per artikel invoeren zonder eerst een bronprijs, veldkoppeling of kortingsregel te configureren.

## Status

Geïmplementeerd en getest op 16 september 2026. Geen bedragen in de actieve leveranciersdatabase gewijzigd.

## Bestanden

- `app/web/purchase_prices.py`: invoerscherm bovenaan tab 5.
- `app/web/dashboard.py`: schermintegratie en toelichting op kortingsregels.
- `app/suppliers/discounts.py`: prijs opslaan/vrijgeven en bescherming tegen kortingsberekeningen.
- `app/suppliers/hub.py`: handmatige prijs behouden tijdens bronimport.
- `tests/test_manual_purchase_price.py`: opslag, validatie, bronimport, kortingsregels en schermtest.

## Flow

Leverancier → Bron & import → 5. Inkoopprijzen → artikel kiezen → netto inkoopprijs excl. btw per verkoopeenheid invullen → Inkoopprijs opslaan. Een ontbrekende prijs blijft leeg totdat de gebruiker een bedrag invoert; nul is een geldig expliciet bedrag. Bestaande verkoopprijs, voorraad en tekstvergrendeling veranderen niet.

De leveranciersservice bewaart de prijs in `cost_price` met herkomst in `raw_data_json.manual_purchase_price`. De bronimport neemt deze waarde over en kortingsberekeningen slaan het artikel over. Handmatige prijs vrijgeven verwijdert de bescherming; het huidige bedrag blijft staan totdat een volgende bronimport of kortingsberekening een nieuwe waarde oplevert.

## Testplan

`PYTHONPYCACHEPREFIX=/tmp/purchase-price-pycache .venv/bin/python -m pytest tests/test_manual_purchase_price.py tests/test_supplier_discounts.py -q -p no:cacheprovider`

Elf tests slagen. De Streamlit-test controleert leeg bedrag, opslaan, melding en wisselen tussen twee artikelen met verschillende verkoopeenheden. Databasetests controleren prijsbehoud bij import en kortingsberekening, vrijgeven en afwijzen van ongeldige bedragen.

## Openstaande punten

Geen automatische prijsafleiding uit de Ultimatron-website. De gebruiker voert de overeengekomen leveranciersprijs in. Verkoopprijsberekening blijft onder tab 6.

## Changelog

- 2026-09-16: directe invoer bovenaan tab 5 toegevoegd; eenheidskoppelingen standaard ingeklapt.
