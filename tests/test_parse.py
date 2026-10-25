"""Parser tests. Fixtures are trimmed copies of the real harristeeter.com
response shapes (2026-10-25 capture), with account ids removed."""

import json
import tempfile
import unittest
from pathlib import Path

from ht_cart import parse


def _line(gtin, qty=1, saved=False):
    return {"gtin13": gtin, "quantity": qty, "savedForLater": saved}


def _product(gtin, name, aisle=None, desc=None, side=None, bay=None, dept="DAIRY",
             promo=None, regular="$3.99"):
    p = {
        "id": gtin,
        "item": {
            "description": name,
            "brand": {"name": "Brand"},
            "customerFacingSize": "12 ct",
            "mainImagePerspective": "front",
            "images": [
                {"perspective": "front", "size": "xlarge", "url": f"x/{gtin}"},
                {"perspective": "front", "size": "medium", "url": f"m/{gtin}"},
            ],
            "familyTree": {"department": {"name": dept}},
        },
        "price": {"storePrices": {"regular": {"defaultDescription": regular}}},
        "location": None,
    }
    if promo:
        p["price"]["storePrices"]["promo"] = {"defaultDescription": promo}
    if aisle:
        p["location"] = {"locations": [{
            "aisle": {"number": aisle, "description": desc, "side": side},
            "bayInAisle": bay, "physicalShelfNumber": "3",
        }]}
    return p


CART = {
    "url": "https://www.harristeeter.com/atlas/v1/carts",
    "body": {"data": {"carts": [{
        "type": "ACTIVE",
        "lineItems": [
            _line("0007203673805", 2),
            _line("0002100004694"),
            _line("0000000004011", 3),
            _line("0007203663104"),
            _line("0081509902165"),
            _line("0009999999999", saved=True),
        ],
    }]}},
}
PRODUCTS = {
    "url": "https://www.harristeeter.com/atlas/v1/product/v2/products?filter.gtin13s=1",
    "body": {"data": {"products": [
        _product("0007203673805", "2% Milk", "60", "DAIRY", "B", "02"),
        _product("0002100004694", "Taco Shells", "1", "AISLE 1", "L", "02",
                 dept="GROC-ALL OTHER"),
        _product("0000000004011", "Bananas", dept="FRUIT",
                 regular="about $1.24 each"),
        _product("0007203663104", "Brown Eggs", "60", "DAIRY", "B", "05"),
        _product("0081509902165", "Tortilla Chips", "6", "AISLE 6", "L", "13",
                 promo="2 For $8.00", regular="$5.79"),
    ]}},
}
SAVINGS = {
    "url": "https://www.harristeeter.com/atlas/v1/carts/v2/cart-savings",
    "body": {"data": {"cartSavings": {
        "lineItems": [
            {"gtin13": "0007203673805", "quantity": 2,
             "totalRegularPrice": "USD 7.18", "totalDiscountedPrice": "USD 1.98"},
            {"gtin13": "0081509902165", "quantity": 1,
             "totalRegularPrice": "USD 5.79", "totalDiscountedPrice": "USD 4.00"},
            {"gtin13": "0002100004694", "quantity": 1,
             "totalRegularPrice": "USD 3.99", "totalDiscountedPrice": "USD 3.99"},
        ],
        "uiPaymentSummary": {"lines": [
            {"type": "SUBTOTAL", "description": "Subtotal (5 items)",
             "amount": "USD 20.00"},
            {"type": "COUPONS", "description": "Coupons (1)", "amount": "USD -2.50"},
            {"type": "TOTAL", "description": "Estimated Total", "amount": "USD 17.50"},
        ]},
    }}},
}
# Same keys, wrong endpoint: must not add cart lines.
OTHER = {
    "url": "https://www.harristeeter.com/atlas/v1/pharmacy/v1/session-cart",
    "body": {"data": {"carts": [{"lineItems": [_line("0001111111111")]}]}},
}


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.result = parse.build([CART, PRODUCTS, SAVINGS, OTHER])
        self.items = self.result["items"]

    def test_only_active_cart_lines(self):
        upcs = [i["upc"] for i in self.items]
        self.assertEqual(len(upcs), 5)
        self.assertNotIn("0009999999999", upcs)  # saved for later
        self.assertNotIn("0001111111111", upcs)  # other endpoint

    def test_sorted_by_aisle_then_bay_then_unlocated(self):
        self.assertEqual(
            [i["name"] for i in self.items],
            ["Taco Shells", "Tortilla Chips", "2% Milk", "Brown Eggs", "Bananas"],
        )

    def test_labels(self):
        labels = [parse.aisle_label(i) for i in self.items]
        self.assertEqual(
            labels,
            ["Aisle 1", "Aisle 6", "Dairy", "Dairy", "No aisle listed"],
        )

    def test_fields(self):
        milk = next(i for i in self.items if i["name"] == "2% Milk")
        self.assertEqual(milk["quantity"], 2)
        self.assertEqual(milk["image"], "m/0007203673805")
        self.assertEqual(milk["location"]["bay"], "02")
        chips = next(i for i in self.items if i["name"] == "Tortilla Chips")
        self.assertEqual((chips["price"], chips["regular_price"]),
                         ("2 For $8.00", "$5.79"))

    def test_line_totals_from_cart_savings(self):
        by = {i["name"]: i for i in self.items}
        self.assertEqual((by["2% Milk"]["line_total"], by["2% Milk"]["line_regular"]),
                         ("$1.98", "$7.18"))
        self.assertEqual(by["Taco Shells"]["line_total"], "$3.99")
        self.assertIsNone(by["Taco Shells"]["line_regular"])  # not discounted
        self.assertIsNone(by["Bananas"]["line_total"])  # not in savings

    def test_summary(self):
        self.assertEqual(
            [(ln["type"], ln["amount"]) for ln in self.result["summary"]],
            [("SUBTOTAL", "$20.00"), ("COUPONS", "-$2.50"), ("TOTAL", "$17.50")],
        )

    def test_money(self):
        self.assertEqual(parse.money("USD 0.99"), "$0.99")
        self.assertEqual(parse.money("USD -2.5"), "-$2.50")
        self.assertEqual(parse.money("about $1.24 each"), "about $1.24 each")

    def test_no_warnings(self):
        self.assertEqual(self.result["warnings"], [])

    def test_missing_product_details(self):
        r = parse.build([CART])
        self.assertEqual(len(r["items"]), 5)
        self.assertIn("No product details for 5 item(s).", r["warnings"])

    def test_empty_capture_warns(self):
        r = parse.build([])
        self.assertEqual(r["items"], [])
        self.assertTrue(r["warnings"])


class RenderTests(unittest.TestCase):
    def test_render_groups_by_aisle(self):
        from ht_cart import scrape, web

        with tempfile.TemporaryDirectory() as d:
            scrape.DATA_DIR = Path(d)
            result = parse.build([CART, PRODUCTS, SAVINGS])
            result["fetched_at"] = "now"
            (Path(d) / "cart.json").write_text(json.dumps(result))
            page = web._render()
        self.assertLess(page.index("Aisle 1"), page.index("Aisle 6"))
        self.assertLess(page.index("Aisle 6"), page.index("Dairy"))
        self.assertEqual(page.count("<h2>Dairy"), 1)
        self.assertIn("Dairy <small>(2)</small>", page)
        self.assertIn("$4.00 <span class=was>$5.79</span>", page)
        self.assertIn("<span class=deal>2 For $8.00</span>", page)
        self.assertIn("<span>Estimated Total</span><span>$17.50</span>", page)
        self.assertIn("<div class='COUPONS neg'>", page)
        self.assertIn("<h2>No aisle listed <small>(1)</small>", page)
        self.assertIn("<button>Refresh</button>", page)


if __name__ == "__main__":
    unittest.main()
