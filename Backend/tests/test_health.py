from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from test_foundation import APITestCase


class HealthTests(APITestCase):
    async def test_readiness_checks_database_without_authentication(self):
        response = await self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready", "database": "available"})

    async def test_database_failure_is_unavailable_without_exposing_error(self):
        error = OperationalError("SELECT 1", {}, Exception("secret connection detail"))
        with patch.object(self.db, "execute", side_effect=error):
            response = await self.client.get("/ready")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"status": "not_ready", "database": "unavailable"})
            self.assertNotIn("secret", response.text)

            # Liveness stays available when a dependency is down.
            response = await self.client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "healthy"})
