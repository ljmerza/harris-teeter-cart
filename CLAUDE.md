# harris_teeter — cart sorted by aisle

Signs in to harristeeter.com with a headful Camoufox browser, reads the cart, and shows the items
on a simple page grouped and sorted by aisle (the site and app sort the cart by when items were added).
Not a git repo yet.

- **Run:** service `harris-teeter-cart` in `../../docker-compose.local.yml` (dcwrap). Credentials:
  `../../env/harris_teeter.env` (`HT_EMAIL`/`HT_PASSWORD`, template in `.env.example`). After code
  changes: `dca build harris-teeter-cart && dca up -d harris-teeter-cart` (code is baked into the
  image, not mounted). UI at `http://192.168.1.76:8107`. `/cart.json` returns the raw result and
  `/health` is the health check.
- **Crossing off is local only.** The ✓ button hides an item (crossed UPCs go to `/data/crossed.json`,
  shared across devices; nothing is sent to Harris Teeter). Undo restores the last one. A
  *successful* Refresh deletes `crossed.json` (a failed one keeps it), and the page asks before
  refreshing while anything is crossed off.
- Dashy tile "Harris Teeter Cart" is under Other Services in `volumes/dashy/conf.yml`.
- **The site is only contacted when Refresh is pressed.** Each refresh launches the browser, opens
  `/signin?redirectUrl=/cart` (skips straight to the cart if the session is still valid), signs in if
  needed, captures every JSON response the cart page loads, and closes the browser.
- **Layout:** `ht_cart/browser.py` (Camoufox launch with a persistent profile and saved fingerprint,
  adapted from `../lowedepot/retailers/browser.py`), `scrape.py` (sign-in + capture), `parse.py`
  (stdlib-only extraction + aisle sort), `web.py` (stdlib HTTP UI). Tests: `python3 -m unittest
  discover -s tests -t .` (no deps needed).
- **State** lives in `../../volumes/harris_teeter_cart/` (bind-mounted at `/data`, root-owned): `profile/` + `profile.identity.json` (the
  persistent browser identity; delete both to start over as a new device), `cart.json` (last
  result), and `captures/<ts>/` (raw JSON, `cart.png`, `cart.html`; the last 5 runs are kept).
  These captures contain account data. Don't commit or share them.
- **Data comes from two responses** (shapes documented at the top of `parse.py`, verified 2026-10-25):
  `/atlas/v1/carts` gives the cart lines (`gtin13`, `quantity`, saved-for-later skipped), and
  `/atlas/v1/product/v2/products` gives name, brand, size, image, the site's own price text, and
  `location.locations[]` (aisle number/description/side, bay, shelf) for the store the account uses.
  `/atlas/v1/carts/v2/cart-savings` gives each line's total after coupons/deals (regular struck
  through) and the summary box (Subtotal, Coupons, Savings, Estimated Total; matches the app).
  Dairy comes back as aisle number 60. Loose and packaged produce come back with `location: null`
  (the site shows no "Located in" for them either) and go in one "No aisle listed" group at the
  end. Items within an aisle sort by bay, so the list walks down the aisle.
- **Sign-in** worked with the generic selectors on the first real run (email + password, no code
  asked). A verification-code prompt stops the run with an error and saves `signin-code.png`.
- To re-parse the latest capture without contacting the site (e.g. after a `parse.py` change), rebuild,
  then rebuild `cart.json` from `/data/captures/<latest>/` with `parse.build(...)` inside the container.
- **Browser pins** (camoufox 0.4.11, playwright 1.61.0, browser 152.0.4-beta.28, driver patch) are
  copied from lowedepot's Dockerfile. Bump them together, deliberately.
- **App reference:** `HarrisTeeter-99.2.apks` is Kroger's shared app with Harris Teeter branding
  (`mobile.kroger.com/mobileatlas/v1/carts`). It embeds Akamai BotManager, so its API can't be
  called without a browser.
- The UI has no auth and is reachable from the LAN; anyone who can reach it can trigger a refresh.
- Don't script repeated refreshes. Bursty traffic is what trips Akamai.
