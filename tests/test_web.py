"""Cross-off flow against a real local server (no browser, no site)."""

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from ht_cart import parse, scrape, web
from tests.test_parse import CART, PRODUCTS, SAVINGS


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


class CrossOffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        scrape.DATA_DIR = Path(self.tmp.name)
        result = parse.build([CART, PRODUCTS, SAVINGS])
        (scrape.DATA_DIR / "cart.json").write_text(json.dumps(result))
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.opener = urllib.request.build_opener(_NoRedirect)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def post(self, path, body=b""):
        req = urllib.request.Request(self.base + path, data=body, method="POST")
        try:
            return self.opener.open(req).status
        except urllib.error.HTTPError as e:
            with e:
                return e.code

    def page(self):
        return urllib.request.urlopen(self.base + "/").read().decode()

    def test_cross_hides_item_and_undo_restores(self):
        self.assertIn("Taco Shells", self.page())
        self.assertEqual(self.post("/cross", b"upc=0002100004694"), 303)
        page = self.page()
        self.assertNotIn("Taco Shells", page)
        self.assertNotIn("<h2>Aisle 1", page)  # its only item is gone
        self.assertIn("<span id=left>4</span> of 5 items left", page)
        self.assertIn("<form id=undo method=post action=/undo>", page)
        self.post("/cross", b"upc=0007203673805")
        self.assertIn("<h2>Dairy <small>(1)</small>", self.page())
        self.post("/undo")
        self.assertIn("<h2>Dairy <small>(2)</small>", self.page())
        self.assertNotIn("Taco Shells", self.page())  # undo is last-in only
        self.post("/undo")
        self.assertIn("Taco Shells", self.page())
        self.assertIn("action=/undo hidden", self.page())

    def test_crossed_stays_local(self):
        self.post("/cross", b"upc=0002100004694")
        self.assertEqual(
            json.loads((scrape.DATA_DIR / "crossed.json").read_text()),
            ["0002100004694"],
        )
        # cart.json (the site's data) is untouched
        cart = json.loads((scrape.DATA_DIR / "cart.json").read_text())
        self.assertIn("0002100004694", [i["upc"] for i in cart["items"]])

    def test_successful_refresh_resets_crossed(self):
        self.post("/cross", b"upc=0002100004694")
        real_run = scrape.run
        scrape.run = lambda: None
        try:
            web._lock.acquire()
            web._refresh_worker()
        finally:
            scrape.run = real_run
        self.assertFalse((scrape.DATA_DIR / "crossed.json").exists())
        self.assertIn("Taco Shells", self.page())

    def test_failed_refresh_keeps_crossed(self):
        self.post("/cross", b"upc=0002100004694")
        real_run = scrape.run

        def boom():
            raise scrape.ScrapeError("nope")

        scrape.run = boom
        try:
            web._lock.acquire()
            web._refresh_worker()
        finally:
            scrape.run = real_run
            web._status["error"] = None
        self.assertNotIn("Taco Shells", self.page())

    def test_side_and_bay_bold(self):
        page = self.page()
        self.assertIn("<b>side L</b> · <b>bay 02</b> · shelf 3", page)


if __name__ == "__main__":
    unittest.main()
