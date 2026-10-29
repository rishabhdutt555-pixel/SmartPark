import os
import tempfile
import unittest
import app as project


class ParkingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        project.DB = os.path.join(self.tmp.name, "test.db")
        project.app.config.update(TESTING=True)
        project.init()
        self.client = project.app.test_client()
        self.client.post("/register", data={"name": "Student", "email": "student@example.com", "password": "Password123"})
        self.client.post("/login", data={"email": "student@example.com", "password": "Password123"})
        self.client.post("/vehicles", data={"plate": "TEST123"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_pages(self):
        for path in ("/", "/vehicles", "/bookings", "/os"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get("/admin").status_code, 403)

    def test_booking_cancel_and_release(self):
        self.client.post("/book", data={"sid": 1, "vid": 1, "hours": 2})
        self.client.post("/book", data={"sid": 1, "vid": 1, "hours": 2})
        with project.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT amount FROM bookings").fetchone()[0], 80)
        self.client.post("/booking/1/cancel")
        with project.connect() as connection:
            self.assertEqual(connection.execute("SELECT status FROM bookings").fetchone()[0], "cancelled")
        self.client.post("/book", data={"sid": 1, "vid": 1, "hours": 1})
        with project.connect() as connection:
            self.assertEqual(connection.execute("SELECT status FROM bookings WHERE id = 2").fetchone()[0], "active")

    def test_os_resource_simulation(self):
        events = project.simulate("concurrent", 5)
        self.assertEqual(len(events), 5)
        self.assertEqual(sum("ALLOCATED" in event for event in events), 1)
        response = self.client.post("/os", data={"task": "resource", "count": 5})
        self.assertEqual(response.status_code, 200)

    def test_cpu_scheduling(self):
        timeline, rows, avg_wait, avg_turn = project.schedule_cpu("fcfs", [5, 3, 1])
        self.assertEqual([item["pid"] for item in timeline], ["P1", "P2", "P3"])
        self.assertEqual([row["waiting"] for row in rows], [0, 5, 8])
        self.assertEqual(avg_wait, 4.33)
        response = self.client.post("/os", data={"task": "cpu", "algorithm": "rr", "bursts": "5,3,1", "quantum": 2})
        self.assertEqual(response.status_code, 200)

    def test_admin(self):
        self.client.post("/logout")
        self.client.post("/login", data={"email": "admin@parking.local", "password": "Admin123!"})
        self.assertEqual(self.client.get("/admin").status_code, 200)
        self.client.post("/admin", data={"label": "P-13", "rate": 50})
        with project.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM spaces").fetchone()[0], 13)


if __name__ == "__main__":
    unittest.main()
