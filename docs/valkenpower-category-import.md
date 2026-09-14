# Valkenpower category import protection

The user-approved `Filters` sheet imported on 2026-09-14 is authoritative for
4,041 existing PIM products. Original values and the complete pre-import database
are in `data/import_reports/valkenpower-categories-20260914T092649066833Z/`.

`app/suppliers/category_locks.py` installs `category_import_locks` and the
`preserve_category_import` SQLite trigger in the Valkenpower database. Each lock
contains all six approved values and import provenance. The trigger restores these
values and their provenance after source imports, enrichment updates, or manual
product writers, while allowing other fields to change. Empty category levels
are intentional and protected too. This also covers workers already running
when protection is installed.

The regular Shopify payload builder gives locked values priority over configured
metafield mappings. Unlocked products retain their existing behavior. To intentionally
change a protected category, update its lock through `install_category_locks`, or
explicitly remove its row from `category_import_locks` and remove the
`category_file_import` marker from the product's raw data before editing it.
Do not alter `official_category_evidence`: it remains the historic website evidence,
distinct from the user's corrected hierarchy.

`scripts/sync_valkenpower_category_import.py` provides prepare/apply/verify phases.
It only calls `metafieldsSet` and `metafieldsDelete` for the six approved `custom`
keys. Empty values are deleted; all nonempty writes use compare-and-set digests.
Existing definitions, the planned products, before values, mutations and full
read-back verification are stored in the supplied report directory. Missing or
ambiguous SKUs are reported instead of creating or guessing products.

## Manual category review (2026-09-14)

The quality policy accepts a locked category import with a nonempty main group,
or an explicit `manual_category_review` for the exact SKU. Shopify ACTIVE status
alone is not approval. The user's confirmed review of GC68CRBLUE, SP50HAL, CP7OS,
BV200FSB and OD01TR is recorded in `manual_category_reviews`. The database trigger
`preserve_manual_category_review` restores this review in raw data after imports.
To revoke approval, delete its table row and remove the raw-data marker in the
same transaction. Other quality, inventory and excluded-collection checks remain
in force. No official website evidence is fabricated by this manual approval.
