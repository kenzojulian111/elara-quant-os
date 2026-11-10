import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import backup


class BackupUtilsTests(unittest.TestCase):
    def test_create_postgres_dump_cleans_partial_file_on_calledprocesserror(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ts = "20260303_010101"
            dump_path = os.path.join(tmpdir, f"postgres_dump_{ts}.sql")
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write("partial")

            with patch.object(backup.Config, "SQLALCHEMY_DATABASE_URI", "postgresql://user:pass@localhost:5432/elara"), \
                 patch.object(backup.Config, "BACKUP_PG_DUMP_TIMEOUT_SEC", 30), \
                 patch("utils.backup.shutil.which", return_value="pg_dump"), \
                 patch("utils.backup.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["pg_dump"], stderr="boom")):
                out = backup._create_postgres_dump(tmpdir, ts)

            self.assertEqual(out, "")
            self.assertFalse(os.path.exists(dump_path))


if __name__ == "__main__":
    unittest.main()

