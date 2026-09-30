import unittest,tempfile,sqlite3
from pathlib import Path
from migrations import migrate
class Upgrade(unittest.TestCase):
 def test_old_users_and_foreign_keys_survive_upgrade(self):
  with tempfile.TemporaryDirectory() as tmp:
   db=Path(tmp)/'old.sqlite3';schema=(Path(__file__).resolve().parents[1]/'schema.sql').read_text().replace(",'student'",'')
   with sqlite3.connect(db) as c:
    c.executescript(schema);c.execute("INSERT INTO tenants VALUES ('t','existing')");c.execute("INSERT INTO branches VALUES ('b','t','branch')");c.execute("INSERT INTO users VALUES ('u','t','b','Original','old@test.local','salt:hash','coach')");c.execute("INSERT INTO staff VALUES ('u','t','monthly',1000)")
   migrate(db);migrate(db)
   with sqlite3.connect(db) as c:
    self.assertEqual(c.execute('SELECT name,email,enabled FROM users').fetchone(),('Original','old@test.local',1));self.assertEqual(c.execute('SELECT id FROM staff').fetchone()[0],'u');self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[]);self.assertIn('superseded_on',[r[1] for r in c.execute('PRAGMA table_info(subscriptions)')])
   self.assertTrue(Path(str(db)+'.before-v2.sqlite3').exists())
