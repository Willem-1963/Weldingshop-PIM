#!/usr/bin/env python3
"""Werk uitsluitend het bestaande conceptartikel over lasclassificaties bij."""

import json

from app.shopify.client import ShopifyClient
from scripts.create_welding_knowledge_blogs import BLOGS, BLOG_HANDLE, blog_id


def main() -> dict:
    client = ShopifyClient.from_settings()
    target_blog = blog_id(client)
    article = next(item for item in BLOGS if item["handle"] == "lasnormeringen-en-classificaties-uitgelegd")
    nodes = client.graphql(
        """query($id:ID!){blog(id:$id){articles(first:250){nodes{id handle title isPublished}}}}""",
        {"id": target_blog},
    )["blog"]["articles"]["nodes"]
    current = next((node for node in nodes if node["handle"] == article["handle"]), None)
    if not current:
        raise RuntimeError(f"Bestaand artikel /blogs/{BLOG_HANDLE}/{article['handle']} niet gevonden.")
    result = client.graphql(
        """mutation($id:ID!,$article:ArticleUpdateInput!){articleUpdate(id:$id,article:$article){
          article{id title handle isPublished} userErrors{code field message}}}""",
        {"id": current["id"], "article": {
            "title": article["title"], "body": article["body"], "summary": article["summary"],
            "tags": article["tags"], "isPublished": False,
        }},
    )["articleUpdate"]
    if result["userErrors"]:
        raise RuntimeError(json.dumps(result["userErrors"], ensure_ascii=False))
    return result["article"]


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
