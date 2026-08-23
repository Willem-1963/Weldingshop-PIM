from app.product_maker_standalone.shopify import _publish_to_all_channels


def test_publish_to_all_available_shopify_channels():
    class Client:
        def __init__(self):
            self.calls = []

        def graphql(self, query, variables=None):
            self.calls.append((query, variables))
            if "publications(first:100)" in query:
                return {"publications": {"nodes": [
                    {"id": "gid://shopify/Publication/1", "name": "Online Store"},
                    {"id": "gid://shopify/Publication/2", "name": "Shop"},
                ]}}
            return {"publishablePublish": {"userErrors": []}}

    client = Client()
    assert _publish_to_all_channels(client, "gid://shopify/Product/10") == 2
    assert client.calls[1][1] == {
        "id": "gid://shopify/Product/10",
        "input": [
            {"publicationId": "gid://shopify/Publication/1"},
            {"publicationId": "gid://shopify/Publication/2"},
        ],
    }
