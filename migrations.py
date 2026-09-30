"""Idempotent schema upgrades. SQLite backup is created before a users rebuild."""
import sqlite3
from pathlib import Path

def migrate(db):
 c=sqlite3.connect(db);c.execute('PRAGMA foreign_keys=OFF')
 try:
  c.execute('BEGIN IMMEDIATE')
  sql=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()[0]
  if "'student'" not in sql:
   backup=Path(str(db)+'.before-v2.sqlite3')
   if not backup.exists():
    with sqlite3.connect(db) as source,sqlite3.connect(backup) as target:source.backup(target)
   c.execute("CREATE TABLE users_v2(id TEXT PRIMARY KEY,tenant TEXT NOT NULL REFERENCES tenants(id),branch TEXT REFERENCES branches(id),name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('super','manager','reception','coach','finance','parent','student')))")
   c.execute('INSERT INTO users_v2 SELECT id,tenant,branch,name,email,password,role FROM users');c.execute('DROP TABLE users');c.execute('ALTER TABLE users_v2 RENAME TO users')
  changes={'users':[('enabled','INTEGER NOT NULL DEFAULT 1')],'subscriptions':[('superseded_on','TEXT'),('tariff','TEXT')],'registrations':[('starts','TEXT'),('expires','TEXT')]}
  for table,columns in changes.items():
   existing={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
   for col,typ in columns:
    if col not in existing:c.execute('ALTER TABLE '+table+' ADD COLUMN '+col+' '+typ)
  if c.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('Migration foreign key validation failed')
  c.commit()
 except: c.rollback();raise
 finally:c.close()
