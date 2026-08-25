import sqlite3

from app.server_backup import (
    create_server_backup, list_server_backups, verify_server_backup,
)


def test_encrypted_backup_contains_consistent_sqlite_snapshot(tmp_path):
    pim = tmp_path / "pim"
    erp_source = tmp_path / "erp-source"
    erp_active = tmp_path / "erp-active"
    (pim / "data" / "database" / "suppliers").mkdir(parents=True)
    (pim / "app").mkdir()
    (pim / "app" / "example.py").write_text("value = 1\n")
    erp_source.mkdir()
    (erp_active / "data").mkdir(parents=True)
    with sqlite3.connect(pim / "data" / "database" / "supplier_registry.sqlite") as db:
        db.execute("create table example(value text)")
        db.execute("insert into example values('veilig')")
    with sqlite3.connect(erp_active / "data" / "weldingshop_erp.sqlite3") as db:
        db.execute("create table example(value text)")
        db.execute("insert into example values('erp')")

    result = create_server_backup(
        "een-sterk-testwachtwoord", backup_dir=tmp_path / "backups",
        pim_root=pim, erp_source_root=erp_source, erp_active_root=erp_active,
        include_server_config=False,
    )

    assert result["database_count"] == 2
    backups = list_server_backups(tmp_path / "backups")
    assert backups[0]["size"] > 0
    assert verify_server_backup(
        backups[0]["path"], backup_dir=tmp_path / "backups"
    )["valid"] is True


def test_backup_rejects_short_password(tmp_path):
    try:
        create_server_backup("kort", backup_dir=tmp_path)
    except ValueError as exc:
        assert "12 tekens" in str(exc)
    else:
        raise AssertionError("Kort wachtwoord werd geaccepteerd")
