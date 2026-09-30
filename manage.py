"""Offline operator commands; never expose this script as a web endpoint."""
import argparse,getpass,sqlite3
from pathlib import Path
import server as app
p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
a=sub.add_parser('tenant');a.add_argument('--name',required=True);a.add_argument('--email',required=True)
b=sub.add_parser('backup');b.add_argument('destination')
args=p.parse_args()
if args.command=='backup':
 dest=Path(args.destination)
 if dest.exists():raise SystemExit('Destination already exists; choose a new filename')
 with app.connect() as source,sqlite3.connect(dest) as target:source.backup(target)
 print('Backup complete:',dest)
else:
 pw=getpass.getpass('New tenant admin password (12+ characters): ')
 if len(pw)<12:raise SystemExit('Password too short')
 with app.connect() as c:
  t=app.uid('TEN-');b=app.uid('B-');app.insert(c,'tenants',id=t,name=args.name);app.insert(c,'branches',id=b,tenant=t,name='الفرع الرئيسي');app.insert(c,'users',id=app.uid('U-'),tenant=t,branch=None,name='مدير الأكاديمية',email=args.email.lower(),password=app.password(pw),role='super')
 print('Created tenant:',t)
 print('For its public registration portal set TIJARO_DEFAULT_TENANT to this ID on its dedicated host.')
