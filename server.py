"""TIJARO Sports: dependency-free reference server. Python 3.11+. See README.md."""
import os, json, sqlite3, uuid, secrets, hashlib, hmac, time, calendar, re
from datetime import date, datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from http.cookies import SimpleCookie
from zoneinfo import ZoneInfo
from migrations import migrate
ROOT=Path(__file__).resolve().parent
DB=os.environ.get('TIJARO_DB',str(ROOT/'tijaro.sqlite3'))
LEVELS=['Level A','Level B','Level 1','Level 2','Level 3','Level 4','Level 5']
SPORTS=['سباحة','كرة قدم','كاراتيه','كيك بوكسينغ']
def uid(prefix=''): return prefix+uuid.uuid4().hex[:16].upper()
def now(): return datetime.now(timezone.utc).isoformat()
def localdate(): return datetime.now(ZoneInfo('Africa/Cairo')).date()
def today(): return localdate().isoformat()
def connect():
 c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
def password(value,salt=None):
 salt=salt or secrets.token_hex(16)
 return salt+':'+hashlib.pbkdf2_hmac('sha256',value.encode(),salt.encode(),310000).hex()
def rows(c,sql,p=()): return [dict(r) for r in c.execute(sql,p)]
def insert(c,table,**v): c.execute(f'INSERT INTO {table} ({",".join(v)}) VALUES ({",".join("?" for _ in v)})',tuple(v.values()))
def require(ok,message='غير مسموح',code=400):
 if not ok: raise Problem(message,code)
class Problem(Exception):
 def __init__(self,message,code=400): self.message=message;self.code=code

def initialize():
 with connect() as c:
  c.executescript((ROOT/'schema.sql').read_text())
  c.commit()
 migrate(DB)
 with connect() as c:
  if c.execute('SELECT 1 FROM tenants').fetchone(): return
  initial=os.environ.get('TIJARO_ADMIN_PASSWORD')
  require(initial and len(initial)>=12,'حدد TIJARO_ADMIN_PASSWORD بكلمة مرور من 12 حرفًا على الأقل')
  insert(c,'tenants',id='academy',name='High Score Sports Academy')
  for id,name in [('b1','التجمع الخامس'),('b2','مدينة نصر'),('b3','الشيخ زايد')]:insert(c,'branches',id=id,tenant='academy',name=name)
  insert(c,'users',id='admin',tenant='academy',branch=None,name='مدير النظام',email=os.environ.get('TIJARO_ADMIN_EMAIL','admin@tijaro.local'),password=password(initial),role='super')

def audit(c,u,action,entity,detail=''):
 insert(c,'audit',id=uid(),tenant=u['tenant'],actor=u['id'],action=action,entity=entity,detail=detail,created=now())
def scope(u,branch=None,coach=None):
 if u['role']=='super':return
 require(u['branch']==branch,'هذا الفرع خارج صلاحيات حسابك',403)
 if u['role']=='coach':require(u['id']==coach,'المجموعة غير مسندة إليك',403)
def group(c,u,id):
 g=c.execute('SELECT * FROM groups_data WHERE id=? AND tenant=?',(id,u['tenant'])).fetchone();require(g,'المجموعة غير موجودة',404);scope(u,g['branch'],g['coach']);return dict(g)
def child(c,u,id):
 ch=c.execute('SELECT * FROM children WHERE id=? AND tenant=?',(id,u['tenant'])).fetchone();require(ch,'المتدرب غير موجود',404)
 m=c.execute('SELECT * FROM memberships WHERE child=? ORDER BY starts DESC,rowid DESC LIMIT 1',(id,)).fetchone()
 if u['role']=='student':require(c.execute('SELECT 1 FROM student_accounts WHERE user_id=? AND child=? AND tenant=?',(u['id'],id,u['tenant'])).fetchone(),'غير مسموح',403)
 elif u['role']=='parent':require(ch['parent']==u['id'],'غير مسموح',403)
 elif m:group(c,u,m['group_id'])
 else:require(u['role']=='super','غير مسموح',403)
 return dict(ch),dict(m) if m else None
def available(c,g):
 count=c.execute('SELECT COUNT(*) FROM memberships WHERE group_id=? AND ends IS NULL',(g['id'],)).fetchone()[0]
 require(count<g['capacity'],'المجموعة مكتملة العدد',409)
def notify(c,u,id,event,message):insert(c,'outbox',id=uid(),tenant=u['tenant'],child=id,event=event,message=message,status='unconfigured',created=now())
def move(c,u,id,target,reason):
 ch,m=child(c,u,id);require(ch['status']=='active' and m and not m['ends'],'لا يوجد تسجيل نشط')
 g=group(c,u,target);require(m['group_id']!=target,'اختر مجموعة مختلفة');available(c,g);require(reason.strip(),'سبب النقل مطلوب')
 stamp=now();c.execute('UPDATE memberships SET ends=? WHERE id=?',(stamp,m['id']))
 insert(c,'memberships',id=uid(),tenant=u['tenant'],child=id,group_id=target,starts=stamp,ends=None)
 insert(c,'transfers',id=uid(),tenant=u['tenant'],child=id,old_group=m['group_id'],new_group=target,reason=reason,actor=u['id'],created=stamp)
 c.execute('UPDATE users SET branch=? WHERE id IN (SELECT user_id FROM student_accounts WHERE child=? AND tenant=?)',(g['branch'],id,u['tenant']))
 audit(c,u,'transfer',id,reason)
def subscription(c,u,id,d):
 starts=d.get('starts',today());expires=d.get('expires',(localdate()+timedelta(days=30)).isoformat())
 begin=date.fromisoformat(starts);end=date.fromisoformat(expires)
 require(begin<=end and end>=localdate(),'تاريخ النهاية يجب أن يساوي البداية أو يليها وألا يكون في الماضي')
 tariff=d.get('tariff') or None
 m=c.execute('SELECT group_id FROM memberships WHERE child=? AND ends IS NULL',(id,)).fetchone();g=group(c,u,m['group_id'])
 if tariff:
  p=c.execute('SELECT * FROM tariffs WHERE id=? AND tenant=? AND enabled=1',(tariff,u['tenant'])).fetchone()
  require(p and p['branch']==g['branch'] and p['sport']==g['sport'] and (not p['level'] or p['level']==g['level']),'الباقة لا تخص الفرع أو المجموعة المختارة')
  credits=p['credits'];amount=p['amount']
 else:
  require(u['role']=='super','اختر باقة معتمدة للفرع؛ التسعير اليدوي للأدمن فقط',403)
  credits=int(d.get('credits',8));amount=int(d.get('amount',0))
 require(1<=credits<=365 and amount>=0,'بيانات الاشتراك غير صحيحة')
 require(not c.execute('SELECT 1 FROM subscriptions WHERE child=? AND starts>? AND superseded_on IS NULL',(id,starts)).fetchone(),'يوجد تجديد يبدأ بعد التاريخ المختار؛ راجع تسلسل الاشتراكات')
 # Original contract dates are immutable. Effective replacement date is stored separately.
 c.execute('UPDATE subscriptions SET superseded_on=? WHERE child=? AND expires>=? AND (superseded_on IS NULL OR superseded_on>?)',(starts,id,starts,starts))
 insert(c,'subscriptions',id=uid(),tenant=u['tenant'],child=id,credits=credits,remaining=credits,starts=starts,expires=expires,amount=amount,tariff=tariff)

def snapshot(c,u):
 t=u['tenant'];r=u['role'];allgroups=rows(c,'SELECT * FROM groups_data WHERE tenant=?',(t,))
 gs=allgroups if r in ('super','parent','student') else [g for g in allgroups if g['branch']==u['branch'] and (r!='coach' or g['coach']==u['id'])]
 kids=rows(c,'SELECT * FROM children WHERE tenant=?',(t,));ms=rows(c,'SELECT * FROM memberships WHERE tenant=? ORDER BY starts,rowid',(t,));gids={g['id'] for g in gs}
 if r=='parent':kids=[k for k in kids if k['parent']==u['id']]
 elif r=='student':
  linked={x['child'] for x in rows(c,'SELECT child FROM student_accounts WHERE tenant=? AND user_id=?',(t,u['id']))};kids=[k for k in kids if k['id'] in linked]
 elif r!='super':
  last={m['child']:m['group_id'] for m in ms};kids=[k for k in kids if last.get(k['id']) in gids]
 ids={k['id'] for k in kids};members=[m for m in ms if m['child'] in ids]
 if r in ('parent','student'):
  own={m['group_id'] for m in members};gs=[g for g in allgroups if g['id'] in own]
 branches=rows(c,'SELECT * FROM branches WHERE tenant=?',(t,))
 if r!='super':
  visible={g['branch'] for g in gs} if r in ('parent','student') else {u['branch']};branches=[b for b in branches if b['id'] in visible]
 res={'user':{k:u[k] for k in ('id','name','email','role','branch')},'branches':branches,'groups':gs,'children':kids,'memberships':members}
 for table in ['subscriptions','attendance','transfers','evaluations','outbox']:res[table]=[x for x in rows(c,f'SELECT * FROM {table} WHERE tenant=?',(t,)) if x['child'] in ids]
 res['staff']=rows(c,'SELECT s.*,u.name,u.branch,u.role FROM staff s JOIN users u ON u.id=s.id WHERE s.tenant=?',(t,)) if r in ('super','finance') else []
 if r=='finance':res['staff']=[s for s in res['staff'] if s['branch']==u['branch']]
 staffids={s['id'] for s in res['staff']}
 for table in ['staff_logs','adjustments']:res[table]=[x for x in rows(c,f'SELECT * FROM {table} WHERE tenant=?',(t,)) if x['staff'] in staffids]
 res['audit']=rows(c,'SELECT * FROM audit WHERE tenant=?',(t,)) if r=='super' else []
 res['registrations']=rows(c,'SELECT * FROM registrations WHERE tenant=?',(t,)) if r in ('super','manager','reception') else []
 if r in ('manager','reception'):res['registrations']=[x for x in res['registrations'] if x['branch']==u['branch']]
 res['parents']=rows(c,"SELECT id,name,email FROM users WHERE tenant=? AND role='parent'",(t,)) if r=='super' else []
 coaches=rows(c,"SELECT id,name,branch FROM users WHERE tenant=? AND role='coach' AND enabled=1",(t,));coachids={g['coach'] for g in gs}
 res['coaches']=coaches if r=='super' else [x for x in coaches if x['id'] in coachids or (r not in ('parent','student') and x['branch']==u['branch'])]
 res['accounts']=rows(c,'SELECT u.id,u.name,u.email,u.role,u.branch,u.enabled,s.child FROM users u LEFT JOIN student_accounts s ON s.user_id=u.id WHERE u.tenant=?',(t,)) if r=='super' else []
 res['tariffs']=rows(c,'SELECT * FROM tariffs WHERE tenant=?',(t,));bids={b['id'] for b in branches};res['tariffs']=[p for p in res['tariffs'] if p['branch'] in bids]
 if r in ('parent','student'):res['tariffs']=[]
 if r=='coach':
  res['tariffs']=[]
  for s in res['subscriptions']:s.pop('amount',None)
 return res

def action(c,u,op,d):
 permissions={'tariff':['super'],'account_update':['super'],'approve':['super','manager','reception'],'link_parent':['super'],'register':['super','manager','reception'],'transfer':['super','manager','reception'],'attendance':['super','manager','reception','coach'],'renew':['super','manager','reception','finance'],'status':['super','manager','reception'],'evaluate':['super','coach'],'group':['super','manager'],'group_update':['super','manager'],'staff':['super'],'staff_log':['super','finance'],'adjust':['super','finance'],'branch':['super'],'user':['super']}
 require(u['role'] in permissions.get(op,[]),'لا تملك صلاحية تنفيذ هذه العملية',403)
 t=u['tenant']
 if op=='approve':
  r=c.execute("SELECT * FROM registrations WHERE id=? AND tenant=? AND status='pending'",(d['registration'],t)).fetchone();require(r,'الطلب غير موجود أو تمت مراجعته',404);scope(u,r['branch']);payload=dict(r);payload.update({'group':r['group_id'],'credits':d.get('credits',8),'amount':d.get('amount',0),'expires':d.get('expires',r['expires'] or (localdate()+timedelta(days=30)).isoformat()),'starts':d.get('starts',r['starts'] or today()),'tariff':d.get('tariff')});result=action(c,u,'register',payload);c.execute("UPDATE registrations SET status='approved' WHERE id=?",(r['id'],));audit(c,u,op,r['id']);return result
 if op=='link_parent':
  ch,m=child(c,u,d['child']);require(c.execute("SELECT 1 FROM users WHERE id=? AND tenant=? AND role='parent'",(d['parent'],t)).fetchone(),'حساب ولي الأمر غير صحيح');c.execute('UPDATE children SET parent=? WHERE id=?',(d['parent'],ch['id']));audit(c,u,op,ch['id']);return {'ok':True}
 if op=='register':
  name=str(d.get('name','')).strip();dob=d.get('dob','');phone=str(d.get('phone','')).strip()
  require(len(name.split())>=3,'أدخل اسم الطفل الثلاثي أو الرباعي');birth=date.fromisoformat(dob);require(birth<=localdate() and birth.year>=1990,'تاريخ الميلاد غير صحيح');require(re.fullmatch(r'\+?[0-9 ]{8,18}',phone),'رقم ولي الأمر غير صحيح')
  g=group(c,u,d['group']);available(c,g);id=uid('TJ-')
  parent=d.get('parent') or None
  if parent:require(c.execute("SELECT 1 FROM users WHERE id=? AND tenant=? AND role='parent'",(parent,t)).fetchone(),'حساب ولي الأمر غير صحيح')
  insert(c,'children',id=id,tenant=t,name=name,dob=dob,phone=phone,email=d.get('email',''),parent=parent,status='active',created=now())
  insert(c,'memberships',id=uid(),tenant=t,child=id,group_id=g['id'],starts=now(),ends=None);subscription(c,u,id,d);audit(c,u,op,id);return {'id':id}
 if op=='transfer':move(c,u,d['child'],d['group'],d.get('reason',''))
 if op=='attendance':
  ch,m=child(c,u,d['child']);require(ch['status']=='active' and m and not m['ends'],'المتدرب غير نشط');g=group(c,u,m['group_id']);day=date.fromisoformat(d.get('day',today()));require(day==localdate(),'التسجيل متاح لحصة اليوم فقط');require(day.weekday() in json.loads(g['days']),'لا توجد حصة لهذه المجموعة اليوم')
  status=d.get('status');require(status in ('present','absent'),'حالة غير صحيحة')
  s=c.execute('SELECT * FROM subscriptions WHERE child=? AND starts<=? AND expires>=? AND (superseded_on IS NULL OR superseded_on>?) ORDER BY rowid DESC LIMIT 1',(ch['id'],today(),today(),today())).fetchone();require(s and s['remaining']>0,'الاشتراك منتهٍ أو بدون رصيد')
  insert(c,'attendance',id=uid(),tenant=t,child=ch['id'],group_id=g['id'],membership=m['id'],subscription=s['id'],day=today(),status=status,actor=u['id'])
  if status=='present':c.execute('UPDATE subscriptions SET remaining=remaining-1 WHERE id=?',(s['id'],))
  notify(c,u,ch['id'],'attendance',f"تم تسجيل {'حضور' if status=='present' else 'غياب'} {ch['name']} بتاريخ {today()}")
  if status=='present' and s['remaining']<=3:notify(c,u,ch['id'],'renewal','رصيد الاشتراك قارب على الانتهاء')
  audit(c,u,op,ch['id'],status)
 if op=='renew':
  ch,m=child(c,u,d['child']);require(ch['status']=='active','أعد تفعيل الطفل أولًا')
  if d.get('group') and d['group']!=m['group_id']:
   require(d.get('starts',today())==today(),'التجديد مع النقل يبدأ اليوم؛ جدولة النقل المستقبلي غير متاحة')
   require(u['role']!='finance','النقل يحتاج صلاحية الإدارة',403);move(c,u,ch['id'],d['group'],d.get('reason','تجديد مع نقل'))
  subscription(c,u,ch['id'],d);audit(c,u,op,ch['id'])
 if op=='status':
  ch,m=child(c,u,d['child']);status=d['status'];require(status in ('active','archived','deleted'),'حالة غير صحيحة')
  if status=='active' and ch['status']!='active':
   g=group(c,u,d['group']);available(c,g);insert(c,'memberships',id=uid(),tenant=t,child=ch['id'],group_id=g['id'],starts=now(),ends=None)
  elif status!='active':c.execute('UPDATE memberships SET ends=? WHERE child=? AND ends IS NULL',(now(),ch['id']))
  c.execute('UPDATE children SET status=? WHERE id=?',(status,ch['id']));audit(c,u,op,ch['id'],status)
 if op=='evaluate':
  ch,m=child(c,u,d['child']);require(m and ch['status']=='active','لا يوجد اشتراك بالمجموعة');score=int(d['score']);require(0<=score<=100 and d.get('skill','').strip(),'التقييم غير صحيح')
  insert(c,'evaluations',id=uid(),tenant=t,child=ch['id'],group_id=m['group_id'],skill=d['skill'],score=score,notes=d.get('notes',''),actor=u['id'],created=now());audit(c,u,op,ch['id'])
 if op in ('group','group_update'):
  original=group(c,u,d['group_id']) if op=='group_update' else None
  if original:d={**d,'branch':original['branch'],'sport':original['sport'],'level':original['level']}
  branch=d['branch'];coach=c.execute("SELECT * FROM users WHERE id=? AND tenant=? AND role='coach' AND enabled=1",(d['coach'],t)).fetchone();require(coach and coach['branch']==branch,'اختر مدربًا مسجلًا في الفرع');scope(u,branch)
  require(d['sport'] in SPORTS and (d['sport']!='سباحة' or d['level'] in LEVELS),'الرياضة أو المستوى غير صحيح');days=sorted(set(map(int,d['days'])));require(days and all(0<=x<=6 for x in days),'اختر أيامًا صحيحة');start=d['start'];end=d['end'];require(re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',start) and re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',end) and start<end,'توقيت غير صحيح')
  for g in rows(c,'SELECT * FROM groups_data WHERE tenant=? AND coach=?',(t,d['coach'])):require((original and g['id']==original['id']) or not(set(days)&set(json.loads(g['days'])) and start<g['end'] and end>g['start']),'المدرب مرتبط بمجموعة أخرى في هذا التوقيت',409)
  require(int(d['capacity'])>0 and d['name'].strip(),'بيانات المجموعة غير صحيحة')
  if original:
   require(c.execute('SELECT COUNT(*) FROM memberships WHERE group_id=? AND ends IS NULL',(original['id'],)).fetchone()[0]<=int(d['capacity']),'السعة أقل من عدد المسجلين');id=original['id'];c.execute('UPDATE groups_data SET name=?,coach=?,days=?,start=?,end=?,capacity=? WHERE id=?',(d['name'],d['coach'],json.dumps(days),start,end,int(d['capacity']),id))
  else:
   id=uid('G-');insert(c,'groups_data',id=id,tenant=t,branch=branch,name=d['name'],sport=d['sport'],level=d['level'] if d['sport']=='سباحة' else '',coach=d['coach'],days=json.dumps(days),start=start,end=end,capacity=int(d['capacity']))
  audit(c,u,op,id)
 if op=='branch':
  require(d['name'].strip(),'اسم الفرع مطلوب');id=uid('B-');insert(c,'branches',id=id,tenant=t,name=d['name']);audit(c,u,op,id)
 if op in ('staff','user'):
  require(d['role'] in ('super','manager','reception','coach','finance','parent','student'),'صلاحية غير صحيحة');require(len(d['password'])>=12 and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',d['email']),'البريد أو كلمة المرور غير صحيح')
  branch=d.get('branch') or None
  if d['role'] in ('manager','reception','coach','finance'):require(branch,'الفرع مطلوب')
  if d['role'] in ('super','parent'):branch=None
  linked=None
  if d['role']=='student':
   linked,m=child(c,u,d['child']);require(m,'المتدرب غير مسجل في مجموعة');branch=group(c,u,m['group_id'])['branch']
  if branch:require(c.execute('SELECT 1 FROM branches WHERE id=? AND tenant=?',(branch,t)).fetchone(),'الفرع غير صحيح')
  id=uid('U-');insert(c,'users',id=id,tenant=t,branch=branch,name=d['name'],email=d['email'].strip().lower(),password=password(d['password']),role=d['role'])
  if linked:insert(c,'student_accounts',user_id=id,child=linked['id'],tenant=t)
  if op=='staff':
   require(d['role'] in ('manager','reception','coach','finance'),'اختر دور موظف');require(d['mode'] in ('monthly','hour','session') and int(d['rate'])>=0,'الأجر غير صحيح');insert(c,'staff',id=id,tenant=t,mode=d['mode'],rate=int(d['rate']))
  audit(c,u,op,id)
 if op=='account_update':
  target=c.execute('SELECT * FROM users WHERE id=? AND tenant=?',(d['account'],t)).fetchone();require(target,'الحساب غير موجود',404)
  enabled=int(d.get('enabled',target['enabled']));require(enabled in (0,1),'حالة الحساب غير صحيحة');require(not(target['id']==u['id'] and not enabled),'لا يمكن تعطيل حسابك الحالي')
  if target['role']=='super' and not enabled:require(c.execute("SELECT COUNT(*) FROM users WHERE tenant=? AND role='super' AND enabled=1 AND id!=?",(t,target['id'])).fetchone()[0]>0,'لا يمكن تعطيل آخر أدمن')
  branch=d.get('branch') or target['branch']
  if target['role'] in ('super','parent'):branch=None
  elif target['role']=='student':branch=target['branch']
  else:require(c.execute('SELECT 1 FROM branches WHERE id=? AND tenant=?',(branch,t)).fetchone(),'الفرع غير صحيح')
  if target['role']=='coach' and (branch!=target['branch'] or not enabled):require(not c.execute('SELECT 1 FROM groups_data WHERE coach=?',(target['id'],)).fetchone(),'أعد إسناد مجموعات المدرب قبل نقله أو تعطيله')
  email=d.get('email',target['email']).strip().lower();require(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email),'البريد غير صحيح')
  c.execute('UPDATE users SET name=?,email=?,branch=?,enabled=? WHERE id=?',(d.get('name',target['name']),email,branch,enabled,target['id']))
  if d.get('password'):
   require(len(d['password'])>=12,'كلمة المرور 12 حرفًا على الأقل')
   if target['id']==u['id']:require(hmac.compare_digest(password(d.get('current_password',''),target['password'].split(':')[0]),target['password']),'كلمة المرور الحالية غير صحيحة')
   c.execute('UPDATE users SET password=? WHERE id=?',(password(d['password']),target['id']))
  # Immediately revoke sessions when identity, scope, password, or account status changes.
  c.execute('DELETE FROM sessions WHERE user_id=?',(target['id'],));audit(c,u,op,target['id'])
 if op=='tariff':
  require(c.execute('SELECT 1 FROM branches WHERE id=? AND tenant=?',(d['branch'],t)).fetchone(),'الفرع غير صحيح');require(d['sport'] in SPORTS,'الرياضة غير صحيحة');level=d.get('level','') if d['sport']=='سباحة' else '';require(not level or level in LEVELS,'المستوى غير صحيح')
  require(d['name'].strip() and 1<=int(d['credits'])<=365 and 1<=int(d['duration'])<=730 and int(d['amount'])>=0,'بيانات الباقة غير صحيحة')
  if d.get('tariff'):
   old=c.execute('SELECT * FROM tariffs WHERE id=? AND tenant=?',(d['tariff'],t)).fetchone();require(old,'الباقة غير موجودة',404)
   c.execute('UPDATE tariffs SET enabled=0 WHERE id=?',(old['id'],))
  id=uid('P-');insert(c,'tariffs',id=id,tenant=t,branch=d['branch'],sport=d['sport'],level=level,name=d['name'],credits=int(d['credits']),duration=int(d['duration']),amount=int(d['amount']));audit(c,u,op,id)
 if op in ('staff_log','adjust'):
  require(c.execute('SELECT 1 FROM staff s JOIN users usr ON usr.id=s.id WHERE s.id=? AND s.tenant=?',(d['staff'],t)).fetchone(),'الموظف غير موجود',404)
  employee=c.execute('SELECT branch FROM users WHERE id=?',(d['staff'],)).fetchone();scope(u,employee['branch'])
  if op=='staff_log':
   day=date.fromisoformat(d.get('day',today()));require(day<=localdate(),'التاريخ في المستقبل');require(d['status'] in ('present','absent'),'حالة غير صحيحة');insert(c,'staff_logs',id=uid(),tenant=t,staff=d['staff'],day=day.isoformat(),status=d['status'],hours=float(d.get('hours',0)) if d['status']=='present' else 0,sessions=int(d.get('sessions',0)) if d['status']=='present' else 0)
  else:
   require(re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',d['month']) and d['reason'].strip(),'الشهر والسبب مطلوبان');insert(c,'adjustments',id=uid(),tenant=t,staff=d['staff'],month=d['month'],amount=int(d['amount']),reason=d['reason'],actor=u['id'])
  audit(c,u,op,d['staff'])
 return {'ok':True}

class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'public'),**kw)
 def log_message(self,*a):pass
 def send(self,code,data,cookie=None):
  self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers();self.wfile.write(json.dumps(data,ensure_ascii=False).encode())
 def auth(self,c):
  cookies=SimpleCookie(self.headers.get('Cookie',''));token=cookies.get('session');token=token.value if token else '';u=c.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>? AND u.enabled=1',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone();require(u,'يرجى تسجيل الدخول',401);return dict(u)
 def do_GET(self):
  if self.path=='/api/health':return self.send(200,{'mode':'server'})
  if self.path=='/api/catalog':
   with connect() as c:
    t=os.environ.get('TIJARO_DEFAULT_TENANT','academy');gs=rows(c,'SELECT * FROM groups_data WHERE tenant=?',(t,))
    for g in gs:
     g['available']=g['capacity']-c.execute('SELECT COUNT(*) FROM memberships WHERE group_id=? AND ends IS NULL',(g['id'],)).fetchone()[0];g.pop('coach',None)
    return self.send(200,{'tenant':t,'branches':rows(c,'SELECT id,name FROM branches WHERE tenant=?',(t,)),'groups':gs,'tariffs':rows(c,'SELECT * FROM tariffs WHERE tenant=? AND enabled=1',(t,))})
  if self.path=='/api/state':
   try:
    with connect() as c:return self.send(200,snapshot(c,self.auth(c)))
   except Problem as e:return self.send(e.code,{'error':e.message})
  if self.path.startswith('/api/'):return self.send(404,{'error':'المسار غير موجود'})
  return super().do_GET()
 def do_POST(self):
  try:
   # Custom header prevents cross-origin HTML form requests; CORS is deliberately disabled.
   require(self.headers.get('X-Tijaro-Request')=='1','طلب غير صالح',403)
   length=int(self.headers.get('Content-Length','0'));require(0<length<=32768,'حجم الطلب غير صالح');d=json.loads(self.rfile.read(length))
   with connect() as c:
    c.execute('BEGIN IMMEDIATE')
    if self.path=='/api/public-register':
     key='registration:'+self.client_address[0];attempts=[x for x in LOGIN_ATTEMPTS.get(key,[]) if x>time.time()-3600];require(len(attempts)<5,'محاولات كثيرة. راجع الاستقبال',429);attempts.append(time.time());LOGIN_ATTEMPTS[key]=attempts
     t=os.environ.get('TIJARO_DEFAULT_TENANT','academy');g=c.execute('SELECT * FROM groups_data WHERE id=? AND tenant=?',(d['group'],t)).fetchone();require(g,'اختر مجموعة صحيحة');require(len(d['name'].strip().split())>=3 and date.fromisoformat(d['dob'])<=localdate() and date.fromisoformat(d['dob']).year>=1990,'اسم الطفل أو تاريخ ميلاده غير صحيح');require(re.fullmatch(r'\+?[0-9 ]{8,18}',d['phone']),'رقم الهاتف غير صحيح');require('@' in d['email'] and len(d['password'])>=12,'البريد أو كلمة المرور غير صحيح');require(date.fromisoformat(d.get('starts',today()))<=date.fromisoformat(d.get('expires',today())),'تواريخ الاشتراك غير صحيحة');parent=uid('U-');insert(c,'users',id=parent,tenant=t,branch=None,name=d.get('parent_name','ولي الأمر'),email=d['email'].lower(),password=password(d['password']),role='parent');insert(c,'registrations',id=uid('R-'),tenant=t,branch=g['branch'],parent=parent,name=d['name'],dob=d['dob'],phone=d['phone'],email=d['email'],group_id=g['id'],status='pending',created=now(),starts=d.get('starts',today()),expires=d.get('expires'));c.commit();return self.send(201,{'ok':True,'message':'تم إنشاء الحساب وإرسال طلب التسجيل للمراجعة. يمكنك تسجيل الدخول؛ يظهر الطفل بعد اعتماد الاستقبال.'})
    if self.path=='/api/login':
     key=self.client_address[0];attempts=LOGIN_ATTEMPTS.get(key,[]);attempts=[x for x in attempts if x>time.time()-300];require(len(attempts)<10,'محاولات كثيرة. انتظر خمس دقائق',429);attempts.append(time.time());LOGIN_ATTEMPTS[key]=attempts
     u=c.execute('SELECT * FROM users WHERE email=?',(str(d.get('email','')).lower(),)).fetchone();candidate=password(str(d.get('password','')),u['password'].split(':')[0] if u else 'dummy');require(u and u['enabled']==1 and hmac.compare_digest(candidate,u['password']),'بيانات الدخول غير صحيحة',401)
     token=secrets.token_urlsafe(32);insert(c,'sessions',token=hashlib.sha256(token.encode()).hexdigest(),user_id=u['id'],expires=int(time.time())+28800);c.commit();return self.send(200,{'ok':True},'session='+token+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'+('; Secure' if os.environ.get('TIJARO_HTTPS')=='1' else ''))
    u=self.auth(c)
    if self.path=='/api/logout':
     cookies=SimpleCookie(self.headers.get('Cookie',''));c.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(cookies['session'].value.encode()).hexdigest(),));c.commit();return self.send(200,{'ok':True},'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
    result=action(c,u,self.path.removeprefix('/api/'),d);c.commit();return self.send(200,result)
  except Problem as e:self.send(e.code,{'error':e.message})
  except sqlite3.IntegrityError:self.send(409,{'error':'تسجيل مكرر أو بيانات متعارضة؛ لم يتم إجراء أي تغيير'})
  except (ValueError,KeyError,TypeError):self.send(400,{'error':'تحقق من الحقول المطلوبة وصيغة البيانات'})
  except Exception:self.send(500,{'error':'تعذر تنفيذ العملية؛ لم تُحفظ التغييرات'})
LOGIN_ATTEMPTS={}
if __name__=='__main__':
 initialize();print('TIJARO running at http://127.0.0.1:8000');ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('PORT','8000'))),Handler).serve_forever()
