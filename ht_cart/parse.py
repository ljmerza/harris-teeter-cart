"""Turn captured JSON responses from the cart page into an aisle-sorted list.

Stdlib only, so it can be tested without the browser stack. Shapes as seen
on harristeeter.com (2026-10-25):

  /atlas/v1/carts            data.carts[type=ACTIVE].lineItems[]
                               {gtin13, quantity, savedForLater}
  /atlas/v1/product/v2/products?filter.gtin13s=...
                             data.products[]
                               id (gtin13), item.{description, brand.name,
                               customerFacingSize, images[], familyTree},
                               price.storePrices.{promo,regular}.defaultDescription,
                               location.locations[]{aisle{number,description,side},
                                                   bayInAisle, physicalShelfNumber}

  /atlas/v1/carts/v2/cart-savings
                             data.cartSavings.lineItems[]
                               {gtin13, totalRegularPrice, totalDiscountedPrice}
                               (line totals after coupons/deals, "USD 3.49")
                             data.cartSavings.uiPaymentSummary.lines[]
                               {type, description, amount} (Subtotal ... Total)

`location` is null for some items (loose produce, some packaged produce);
those go in one "No aisle listed" group at the end.
"""

import re


def _cart_lines(captures):
    """gtin13 -> quantity, from the active cart. Saved-for-later is skipped."""
    qty = {}
    for cap in captures:
        if not re.search(r"/atlas/v1/carts(\?|$)", cap.get("url") or ""):
            continue
        carts = ((cap.get("body") or {}).get("data") or {}).get("carts") or []
        for cart in carts:
            if cart.get("type") not in (None, "ACTIVE"):
                continue
            for li in cart.get("lineItems") or []:
                if li.get("savedForLater") or not li.get("gtin13"):
                    continue
                qty[li["gtin13"]] = li.get("quantity") or 0
    return qty


def _products(captures):
    """gtin13 -> product dict, from every products response."""
    out = {}
    for cap in captures:
        if "/atlas/v1/product/v2/products" not in (cap.get("url") or ""):
            continue
        for p in ((cap.get("body") or {}).get("data") or {}).get("products") or []:
            if p.get("id"):
                out.setdefault(p["id"], p)
    return out


def money(v):
    """'USD -2.50' -> '-$2.50'. Anything unparseable is returned as-is."""
    m = re.fullmatch(r"\s*(?:USD)?\s*(-?)\$?(\d+(?:\.\d+)?)\s*", v or "")
    return f"{m.group(1)}${float(m.group(2)):.2f}" if m else v


def _savings(captures):
    """(gtin13 -> (regular_total, discounted_total), summary lines)."""
    totals, summary = {}, []
    for cap in captures:
        if "/atlas/v1/carts/v2/cart-savings" not in (cap.get("url") or ""):
            continue
        cs = ((cap.get("body") or {}).get("data") or {}).get("cartSavings") or {}
        for li in cs.get("lineItems") or []:
            if li.get("gtin13"):
                totals[li["gtin13"]] = (
                    money(li.get("totalRegularPrice")),
                    money(li.get("totalDiscountedPrice")),
                )
        lines = (cs.get("uiPaymentSummary") or {}).get("lines") or []
        if lines:
            summary = [
                {
                    "type": ln.get("type"),
                    "description": ln.get("description"),
                    "amount": money(ln.get("amount")),
                }
                for ln in lines
            ]
    return totals, summary


def _image(item):
    imgs = item.get("images") or []
    persp = item.get("mainImagePerspective") or "front"
    for want in ("medium", "small", "large", "xlarge"):
        for img in imgs:
            if img.get("perspective") == persp and img.get("size") == want:
                return img.get("url")
    return imgs[0].get("url") if imgs else None


def _price(p):
    """(shown, regular_if_on_sale) as the site's own display strings."""
    sp = (p.get("price") or {}).get("storePrices") or {}
    promo = (sp.get("promo") or {}).get("defaultDescription")
    regular = (sp.get("regular") or {}).get("defaultDescription")
    if promo and promo != regular:
        return promo, regular
    return regular, None


def _location(loc):
    aisle = loc.get("aisle") or {}
    return {
        "aisle": aisle.get("number"),
        "description": aisle.get("description"),
        "side": aisle.get("side"),
        "bay": loc.get("bayInAisle"),
        "shelf": loc.get("physicalShelfNumber"),
    }


def _department(item):
    name = (((item.get("familyTree") or {}).get("department") or {}).get("name")) or ""
    return name.title() or None


def build(captures):
    captures = list(captures)
    qty = _cart_lines(captures)
    products = _products(captures)
    totals, summary = _savings(captures)
    warnings = []
    if not qty:
        warnings.append("No cart lines found in the /atlas/v1/carts response.")

    items = []
    for gtin, q in qty.items():
        p = products.get(gtin) or {}
        item = p.get("item") or {}
        locs = [_location(x) for x in ((p.get("location") or {}).get("locations") or [])]
        price, was = _price(p)
        regular_total, line_total = totals.get(gtin, (None, None))
        items.append(
            {
                "upc": gtin,
                "quantity": q,
                "name": item.get("description") or gtin,
                "brand": (item.get("brand") or {}).get("name"),
                "size": item.get("customerFacingSize"),
                "price": price,
                "regular_price": was,
                "line_total": line_total,
                "line_regular": regular_total if regular_total != line_total else None,
                "image": _image(item),
                "department": _department(item),
                "location": locs[0] if locs else None,
                "other_locations": locs[1:],
            }
        )

    missing_product = [i["upc"] for i in items if i["upc"] not in products]
    if missing_product:
        warnings.append(f"No product details for {len(missing_product)} item(s).")
    items.sort(key=sort_key)
    return {"items": items, "summary": summary, "warnings": warnings}


def _int(s):
    m = re.match(r"\s*(\d+)", s or "")
    return int(m.group(1)) if m else None


def sort_key(item):
    """Located items by aisle number, then bay (walking down the aisle), then
    side and shelf. Items with no location go last, by department."""
    loc = item.get("location")
    name = (item.get("name") or "").lower()
    if not loc:
        return (1, 0, (item.get("department") or "~").lower(), 0, "", 0, name)
    num = _int(loc.get("aisle"))
    return (
        0,
        num if num is not None else 10**6,
        "" if num is not None else (loc.get("description") or "").lower(),
        _int(loc.get("bay")) or 0,
        (loc.get("side") or "").lower(),
        _int(loc.get("shelf")) or 0,
        name,
    )


def aisle_label(item):
    """Group heading for an item: 'Aisle 3', 'Dairy', or 'No aisle listed'."""
    loc = item.get("location")
    if not loc:
        return "No aisle listed"
    desc = (loc.get("description") or "").strip()
    num = loc.get("aisle")
    if not desc or re.fullmatch(r"AISLE\s*\d+", desc, re.I):
        return f"Aisle {num}" if num else "Unknown aisle"
    return desc.title()
