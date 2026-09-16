"""Simple per-article net purchase price editor for supplier tab 5."""
import streamlit as st

from app.suppliers.discounts import (
    apply_mapped_purchase_costs, discount_product_options, release_manual_purchase_cost, save_manual_purchase_cost,
)
from app.suppliers.hub import get_supplier_product


def render_manual_purchase_price(slug: str, source_field: str = '') -> None:
    st.markdown('#### Inkoopprijs per artikel')
    if source_field:
        st.info(f'De netto inkoopprijs exclusief btw wordt overgenomen uit de bronkolom: {source_field}.')
        if st.button('Inkoopprijzen uit bron bijwerken', key=f'apply_source_cost_{slug}'):
            result = apply_mapped_purchase_costs(slug)
            st.success(f"{result['updated']} inkoopprijzen bijgewerkt; {result['missing']} zonder geldige bronprijs; {result['manual']} handmatig vastgelegd.")
            st.session_state.pop(f'discount_preview_{slug}', None)
            st.session_state.pop(f'sales_price_preview_{slug}', None)
    else:
        st.caption('Vul de netto inkoopprijs exclusief btw in: het bedrag dat je na leverancierskorting betaalt, per verkoopeenheid.')
    flash_key = f'purchase_price_saved_{slug}'
    if flash_key in st.session_state:
        st.success(st.session_state.pop(flash_key))
    options = discount_product_options(slug)
    if not options:
        st.info('Haal eerst producten op of importeer een prijslijst. Daarna kun je hier per artikel de inkoopprijs invullen.')
        return
    sku = st.selectbox(
        'Artikel — zoek op artikelnummer of productnaam', list(options),
        format_func=lambda key: f"{key} — {options[key].get('source_title') or ''}",
        key=f'purchase_price_article_{slug}',
    )
    product = get_supplier_product(slug, sku)
    if not product:
        st.info('Dit artikel is niet meer beschikbaar. Vernieuw de pagina.')
        return
    unit = product.get('sales_unit') or 'stuk'
    current = product.get('cost_price')
    manual = (product.get('raw_data') or {}).get('manual_purchase_price')
    st.metric(f'Huidige netto inkoopprijs per {unit}',
              'Nog niet ingesteld' if current is None else f'€ {float(current):.2f}'.replace('.', ','))
    if source_field:
        source_value = (product.get('raw_data') or {}).get(source_field)
        st.caption(f'Bronwaarde {source_field}: {source_value if source_value is not None else "ontbreekt"}')
        if not st.toggle('Inkoopprijs handmatig aanpassen', value=bool(manual), key=f'manual_cost_toggle_{slug}_{sku}'):
            st.divider()
            return
    if manual:
        st.caption('Handmatig vastgelegd. Deze prijs blijft behouden bij bronimport, verrijking en het toepassen van kortingsregels.')
    elif current is None:
        st.info('Voor dit artikel ontbreekt de inkoopprijs. Vul hieronder je leveranciersprijs in.')
    purchase_unit = product.get('purchase_unit') or 'stuk'
    factor = product.get('purchase_units_per_sales_unit') or 1
    if purchase_unit != unit or float(factor) != 1:
        st.caption(f'Inkoopeenheid: {purchase_unit} · Verkoopeenheid: {unit} · Omrekenfactor: {factor}. Vul hier de uiteindelijke kostprijs per {unit} in.')
    with st.form(f'manual_purchase_cost_{slug}_{sku}'):
        amount = st.number_input(
            f'Netto inkoopprijs per {unit} (€ excl. btw)', min_value=0.0,
            value=float(current) if current is not None else None,
            step=0.01, format='%.2f', placeholder='Bijvoorbeeld 249,50',
            key=f'purchase_price_amount_{slug}_{sku}',
        )
        st.caption('Opslaan werkt de inkoopprijs in PIM bij. De verkoopprijs stel je in onder tab 6.')
        submitted = st.form_submit_button('Inkoopprijs opslaan', type='primary')
    if submitted:
        try:
            cost = save_manual_purchase_cost(slug, sku, amount)
            formatted = f'{cost:.2f}'.replace('.', ',')
            st.session_state[flash_key] = f'Inkoopprijs voor {sku} opgeslagen: € {formatted} excl. btw per {unit}.'
            st.session_state.pop(f'discount_preview_{slug}', None)
            st.session_state.pop(f'sales_price_preview_{slug}', None)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if manual and st.button('Handmatige prijs vrijgeven voor bronimport en kortingsregels', key=f'release_purchase_price_{slug}_{sku}'):
        release_manual_purchase_cost(slug, sku)
        st.session_state[flash_key] = 'De huidige prijs blijft staan totdat een bronimport of kortingsregel een nieuwe inkoopprijs berekent.'
        st.rerun()
    st.divider()
