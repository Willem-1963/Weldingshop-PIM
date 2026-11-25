from __future__ import annotations

import pandas as pd
import streamlit as st

from app.suppliers.canonical_product_fields import (
    list_canonical_brands, list_canonical_filter_values,
    sync_canonical_brands_to_shopify, sync_canonical_filter_values_to_shopify,
    upsert_canonical_brand, upsert_canonical_filter_value,
)


def show_canonical_product_fields_page() -> None:
    st.title("Canonieke productvelden")
    st.caption(
        "Merchant-managed productdata die losstaat van leverancier en via de "
        "normale PIM-sync naar Shopify gaat. Merk gebruikt custom.merk; vendor "
        "blijft leverancier."
    )
    query = st.text_input("Zoek op SKU, handle of merk")
    records = list_canonical_brands(query)
    st.metric("Canonieke merkrecords", len(records))
    if records:
        frame = pd.DataFrame(records)[
            ["product_gid", "shopify_handle", "skus", "brand", "evidence", "updated_at"]
        ]
        edited = st.data_editor(
            frame,
            width="stretch",
            hide_index=True,
            disabled=["product_gid", "shopify_handle", "skus", "updated_at"],
            column_config={
                "product_gid": "Shopify product-GID",
                "shopify_handle": "Handle",
                "skus": "SKU's",
                "brand": "Merk",
                "evidence": "Bronbewijs",
                "updated_at": "Bijgewerkt",
            },
            key="canonical_brand_editor",
        )
        if st.button("Merkcorrecties opslaan", type="primary"):
            for row in edited.to_dict("records"):
                upsert_canonical_brand(
                    row["product_gid"], row["shopify_handle"],
                    str(row["skus"] or "").split(" | "), row["brand"], row["evidence"],
                )
            st.success("Canonieke merken opgeslagen.")
            st.rerun()
        if st.button("Canonieke merken naar Shopify synchroniseren"):
            result = sync_canonical_brands_to_shopify()
            st.success(
                f"{result['records']} merken gecontroleerd; "
                f"{result['writes']} Shopify-wijzigingen uitgevoerd."
            )
    st.divider()
    st.subheader("Canonieke koopfilters")
    st.caption(
        "Technische koopcriteria voor XCloud. De PIM bewaart de productwaarde; "
        "normale leverancierssyncs nemen deze velden zonder leveranciersoverschrijving mee."
    )
    filter_records = list_canonical_filter_values(query)
    st.metric("Canonieke filterwaarden", len(filter_records))
    if filter_records:
        filter_frame = pd.DataFrame(filter_records)[
            ["product_gid", "shopify_handle", "skus", "field_key", "field_type",
             "field_value", "evidence", "updated_at"]
        ]
        filter_edited = st.data_editor(
            filter_frame, width="stretch", hide_index=True,
            disabled=["product_gid", "shopify_handle", "skus", "field_key",
                      "field_type", "updated_at"],
            column_config={
                "product_gid": "Shopify product-GID", "shopify_handle": "Handle",
                "skus": "SKU's", "field_key": "Filterveld",
                "field_type": "Shopify-type", "field_value": "Canonieke waarde",
                "evidence": "Bronbewijs", "updated_at": "Bijgewerkt",
            }, key="canonical_filter_editor",
        )
        if st.button("Filtercorrecties opslaan", type="primary"):
            for row in filter_edited.to_dict("records"):
                upsert_canonical_filter_value(
                    row["product_gid"], row["shopify_handle"],
                    str(row["skus"] or "").split(" | "), row["field_key"],
                    row["field_type"], row["field_value"], row["evidence"],
                )
            st.success("Canonieke filterwaarden opgeslagen.")
            st.rerun()
        if st.button("Canonieke filters naar Shopify synchroniseren"):
            result = sync_canonical_filter_values_to_shopify()
            st.success(
                f"{result['records']} filterwaarden gecontroleerd; "
                f"{result['writes']} Shopify-wijzigingen uitgevoerd."
            )
    with st.expander("Nieuw canoniek merkrecord"):
        with st.form("new_canonical_brand"):
            gid = st.text_input("Shopify product-GID")
            handle = st.text_input("Shopify handle")
            skus = st.text_area("SKU's, één per regel")
            brand = st.text_input("Merk")
            evidence = st.text_area("Bronbewijs")
            if st.form_submit_button("Record toevoegen", type="primary"):
                upsert_canonical_brand(
                    gid, handle, skus.splitlines(), brand, evidence,
                )
                st.success("Canoniek merkrecord toegevoegd.")
                st.rerun()
