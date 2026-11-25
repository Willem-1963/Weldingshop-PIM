from app.shopify.internal_components import (
    internal_component_signals, is_internal_component, publication_cleanup_plan,
)


def product(*, sku="STOCK-123", vendor="OpenClaw Stock Component", product_type="Internal stock component", published=True):
    return {"id":"p1","vendor":vendor,"productType":product_type,
            "variants":{"nodes":[{"id":"v1","sku":sku}]},
            "resourcePublicationsV2":{"nodes":[{"isPublished":published,"publication":{"id":"pub1","name":"Online Store"}}]}}


def test_requires_all_three_strong_signals():
    assert is_internal_component(product())
    assert not is_internal_component(product(sku="123"))
    assert not is_internal_component(product(vendor="Rhodius"))
    assert not is_internal_component(product(product_type="Abrasive"))


def test_normal_active_product_is_not_protected():
    normal=product(sku="210633",vendor="Rhodius",product_type="Lamellenslijpschijf")
    assert internal_component_signals(normal)=={"sku_prefix":False,"vendor":False,"product_type":False}
    assert not is_internal_component(normal)


def test_cleanup_plan_only_unpublishes_current_publications_and_preserves_status():
    hidden=product(); hidden["status"]="ACTIVE"
    already=product(sku="STOCK-456",published=False); already["id"]="p2"; already["status"]="ACTIVE"
    mismatch=product(vendor="Rhodius")
    plan=publication_cleanup_plan({"protected":[hidden,already],"mismatched":[mismatch]})
    assert plan["rows"]==[{"id":"p1","input":[{"publicationId":"pub1"}]}]
    assert plan["protected_stock_skus"]==["STOCK-123","STOCK-456"]
    assert plan["mismatched"]==[mismatch]
    assert hidden["status"]=="ACTIVE"
