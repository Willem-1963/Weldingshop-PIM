# Certilas verkoopbeleid per fysieke verpakking

## Bron van waarheid
De PIM-database bevat het beleid in drie lagen:

- `certilas_order_units`: letterlijke portalregel en portalminimum;
- `certilas_sales_policy_config`: instelbare, leveranciersbrede drempels;
- `certilas_sales_unit_policies`: effectieve beslissing per SKU/EAN met bewijs, bronhash en versienummer.

Handmatige afwijkingen staan in `certilas_sales_policy_overrides`; wijzigingen worden gelogd in de bijbehorende history-tabellen. SKU-keuzes staan niet in code.

## Standaardbeslissing

- Het fysieke pak komt uit de exacte PIM-SKU/EAN.
- Het leveranciersminimum wordt naar boven afgerond op een geheel aantal fysieke pakken.
- Een SKU mag per pak wanneer de instelbare omloopdrempel is gehaald of een herleidbare override bestaat.
- Ontbreekt het fysieke pak, dan wordt de policy `blocked`.

## Herberekenen

Eerst verkoopbewijs vernieuwen, daarna:

`PYTHONPATH=. .venv/bin/python scripts/rebuild_certilas_sales_policies.py --sales-evidence <audit.json> --apply`

## Aanpassen

Per SKU:

`PYTHONPATH=. .venv/bin/python scripts/set_certilas_sales_policy.py override --sku <SKU> --mode single_pack --packs 1 --reason '<reden>'`

Drempelinstelling:

`PYTHONPATH=. .venv/bin/python scripts/set_certilas_sales_policy.py config --set minimum_distinct_paid_orders=3 --reason '<reden>'`

Na een aanpassing: policies herberekenen, Shopify via de bestaande apply-route plannen/toepassen en naar ERP spiegelen.
