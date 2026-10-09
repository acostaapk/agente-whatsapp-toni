import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from admin.dedupe import Dedupe


class DedupeTests(unittest.TestCase):
    def test_same_id_twice_is_duplicate(self):
        with TemporaryDirectory() as tmp:
            d = Dedupe(Path(tmp) / "seen.json")
            self.assertFalse(d.already_seen("wamid.1"))
            self.assertTrue(d.already_seen("wamid.1"))
            self.assertFalse(d.already_seen(""))
            self.assertFalse(d.already_seen("wamid.2"))

    def test_state_survives_restart_and_prunes_expired(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "seen.json"
            t0 = time.time()
            d1 = Dedupe(p)
            self.assertFalse(d1.already_seen("wamid.9", now=t0))
            d2 = Dedupe(p)  # nova instância, mesmo arquivo
            self.assertTrue(d2.already_seen("wamid.9", now=t0 + 100))
            d3 = Dedupe(p)
            self.assertFalse(d3.already_seen("wamid.9", now=t0 + 25 * 3600))

    def test_same_text_within_window_is_duplicate(self):
        with TemporaryDirectory() as tmp:
            d = Dedupe(Path(tmp) / "seen.json")
            self.assertFalse(d.same_text_recent("5511999990001", "Quero comprar"))
            self.assertTrue(d.same_text_recent("5511999990001", "  quero   COMPRAR "))
            self.assertFalse(d.same_text_recent("5511999990002", "Quero comprar"))
            self.assertFalse(d.same_text_recent("5511999990001", "Outro texto"))

    def test_empty_text_and_window_expiry_never_dupe(self):
        with TemporaryDirectory() as tmp:
            d = Dedupe(Path(tmp) / "seen.json")
            self.assertFalse(d.same_text_recent("5511999990001", ""))
            self.assertFalse(d.same_text_recent("5511999990001", ""))
            self.assertFalse(d.same_text_recent("", "oi"))
            self.assertFalse(d.same_text_recent("5511999990001", "oi", now=1000))
            self.assertFalse(
                d.same_text_recent("5511999990001", "oi", now=1000 + 31)
            )


if __name__ == "__main__":
    unittest.main()
