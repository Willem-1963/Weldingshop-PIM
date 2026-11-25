from __future__ import annotations

import json
from app.shopify.certilas_loyalty import expire_customer_credits
from app.shopify.certilas_pos_prices import run as sync_pos_prices
from app.shopify.return_discount import expire_return_credits
from app.shopify.return_discount_catalog import run as sync_return_discount_catalog


def main() -> None:
    print(json.dumps({
        "pos_prices": sync_pos_prices(True),
        "expired_certilas_credits": expire_customer_credits(),
        "return_discount_catalog": sync_return_discount_catalog(True),
        "expired_return_credits": expire_return_credits(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
