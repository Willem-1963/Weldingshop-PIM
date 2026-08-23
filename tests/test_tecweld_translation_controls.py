import sqlite3

from app.suppliers import tecweld_dutch_chain as chain


def _connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_product_button_job_excludes_documents(monkeypatch, tmp_path):
    database = tmp_path / "tecweld.sqlite"
    monkeypatch.setattr(chain, "init_supplier_database", lambda slug: database)
    monkeypatch.setattr(chain, "_connect", _connect)
    with _connect(database) as connection:
        connection.execute(
            """CREATE TABLE products(
               sku TEXT PRIMARY KEY,source_present INTEGER,raw_data_json TEXT,
               ai_title TEXT,product_group_name TEXT,html_description TEXT)"""
        )
        connection.execute(
            "INSERT INTO products VALUES('TEC-1',1,'{}',NULL,'Pressure regulators',NULL)"
        )
        chain.init_tables(connection)

    job_id = chain.create_job(include_documents=False)

    with _connect(database) as connection:
        job = connection.execute(
            "SELECT * FROM dutch_content_jobs WHERE id=?", (job_id,)
        ).fetchone()
        item_types = connection.execute(
            "SELECT DISTINCT item_type FROM dutch_content_job_items WHERE job_id=?",
            (job_id,),
        ).fetchall()
    assert job["total_products"] == 1
    assert job["total_documents"] == 0
    assert job["message"] == "Nederlandse Tecweld-productvertaling voorbereid"
    assert [row[0] for row in item_types] == ["product"]
