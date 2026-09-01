from app.suppliers.website_enrichment import _has_current_dutch_enrichment


def _item(version):
    return {
        "current_raw_data": {
            "website_enrichment": {
                "language": "nl", "enrichment_version": version,
            }
        }
    }


def test_current_numeric_enrichment_version_is_recognized():
    assert _has_current_dutch_enrichment(_item(2))
    assert _has_current_dutch_enrichment(_item("2"))


def test_legacy_provenance_label_does_not_break_family_screen():
    assert not _has_current_dutch_enrichment(_item("official-manual-restore-v1"))
