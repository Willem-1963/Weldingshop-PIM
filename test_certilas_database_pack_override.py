import json, sqlite3, pytest
from app.suppliers.hub import _certilas_database_package_weight

def test_certilas_database_package_weight_is_ean_pinned():
    c=sqlite3.connect(":memory:")
    c.execute("create table certilas_pack_overrides(sku text primary key,rule_json text not null)")
    c.execute("insert into certilas_pack_overrides values(?,?)",("10022",json.dumps({"ean":"8720682050347","kg_per_pack":2.2})))
    assert _certilas_database_package_weight(c,"10022","8720682050347")==2.2
    with pytest.raises(ValueError,match="afwijkend EAN"):
        _certilas_database_package_weight(c,"10022","wrong")
    assert _certilas_database_package_weight(c,"missing","8720682050347") is None
