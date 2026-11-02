from html.parser import HTMLParser
from test_foundation import APITestCase


class PageStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.password_types = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "input" and attrs.get("id") == "password":
            self.password_types.append(attrs.get("type"))
        if tag == "script":
            self.scripts.append(attrs.get("src"))


class StudentAppTests(APITestCase):
    async def test_same_origin_app_and_assets_are_served(self):
        response = await self.client.get("/app/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        parsed = PageStructure()
        parsed.feed(response.text)
        self.assertEqual(len(parsed.ids), len(set(parsed.ids)))
        self.assertEqual(parsed.password_types, ["password"])
        self.assertEqual(parsed.scripts, ["./app.js"])
        for endpoint in ["/app/app.js", "/app/styles.css"]:
            response = await self.client.get(endpoint)
            self.assertEqual(response.status_code, 200)

    async def test_static_route_does_not_expose_backend_files(self):
        for path in ["/app/.env", "/app/..%2F..%2FBackend%2F.env", "/app/..%2F..%2FBackend%2Fapp%2Fcore%2Fconfig.py"]:
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 404)

    async def test_document_response_identifies_owner_for_processing_controls(self):
        response = await self.client.get("/documents", headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["owner_id"], str(self.owner.id))
