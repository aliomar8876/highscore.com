import unittest,tempfile,os,json,sys,sqlite3
from datetime import date,timedelta
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
import server as app
class Engine(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();app.DB=self.tmp.name+'/test.sqlite3';os.environ['TIJARO_ADMIN_PASSWORD']='test-password-123456';app.initialize();self.c=app.connect();self.u=dict(self.c.execute("SELECT * FROM users WHERE id='admin'").fetchone())
  for tenant in ('other',):app.insert(self.c,'tenants',id=tenant,name=tenant)
  app.action(self.c,self.u,'staff',{'name':'Coach','email':'coach@test.local','password':'test-password-123456','role':'coach','branch':'b1','mode':'hour','rate':100})
  self.coach=self.c.execute("SELECT id FROM users WHERE role='coach'").fetchone()[0]
  for n,start,end,branch in [('a','16:00','17:00','b1'),('b','17:00','18:00','b1')]:app.action(self.c,self.u,'group',{'name':n,'branch':branch,'sport':'سباحة','level':'Level A','coach':self.coach,'days':[date.today().weekday()],'start':start,'end':end,'capacity':2})
  self.gs=app.rows(self.c,'SELECT * FROM groups_data');self.k=self.register('أحمد محمد علي');self.c.commit()
 def register(self,name):return app.action(self.c,self.u,'register',{'name':name,'dob':'2018-01-31','phone':'01000000000','group':self.gs[0]['id'],'credits':8,'amount':800})['id']
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def test_transfer_preserves_attendance_uid_and_balance(self):
  app.action(self.c,self.u,'attendance',{'child':self.k,'status':'present'});before=app.rows(self.c,'SELECT * FROM attendance');app.action(self.c,self.u,'transfer',{'child':self.k,'group':self.gs[1]['id'],'reason':'ترقية'})
  self.assertEqual(app.rows(self.c,'SELECT * FROM attendance'),before);self.assertEqual(before[0]['group_id'],self.gs[0]['id']);self.assertEqual(self.c.execute('SELECT remaining FROM subscriptions').fetchone()[0],7);self.assertEqual(self.c.execute('SELECT COUNT(*) FROM memberships WHERE child=?',(self.k,)).fetchone()[0],2)
 def test_duplicate_attendance_cannot_deduct_twice(self):
  app.action(self.c,self.u,'attendance',{'child':self.k,'status':'present'})
  with self.assertRaises(sqlite3.IntegrityError):app.action(self.c,self.u,'attendance',{'child':self.k,'status':'present'})
  self.assertEqual(self.c.execute('SELECT remaining FROM subscriptions').fetchone()[0],7)
 def test_absence_no_deduction(self):
  app.action(self.c,self.u,'attendance',{'child':self.k,'status':'absent'});self.assertEqual(self.c.execute('SELECT remaining FROM subscriptions').fetchone()[0],8)
 def test_capacity(self):
  self.register('عمر محمد علي')
  with self.assertRaises(app.Problem):self.register('يوسف محمد علي')
 def test_coach_conflict(self):
  with self.assertRaises(app.Problem):app.action(self.c,self.u,'group',{'name':'collision','branch':'b1','sport':'سباحة','level':'Level A','coach':self.coach,'days':[date.today().weekday()],'start':'16:30','end':'17:30','capacity':5})
 def test_tenant_and_branch_isolation(self):
  outsider={**self.u,'tenant':'other'}
  with self.assertRaises(app.Problem):app.child(self.c,outsider,self.k)
  manager={**self.u,'role':'manager','branch':'b2'}
  with self.assertRaises(app.Problem):app.child(self.c,manager,self.k)
  self.assertEqual(app.snapshot(self.c,manager)['children'],[])
 def test_coach_cannot_transfer(self):
  coach=dict(self.c.execute('SELECT * FROM users WHERE id=?',(self.coach,)).fetchone())
  with self.assertRaises(app.Problem):app.action(self.c,coach,'transfer',{'child':self.k,'group':self.gs[1]['id'],'reason':'x'})
 def test_archive_frees_seat_and_preserves_history(self):
  app.action(self.c,self.u,'attendance',{'child':self.k,'status':'present'});app.action(self.c,self.u,'status',{'child':self.k,'status':'archived'});self.assertEqual(self.c.execute('SELECT COUNT(*) FROM memberships WHERE ends IS NULL').fetchone()[0],0);self.assertEqual(self.c.execute('SELECT COUNT(*) FROM attendance').fetchone()[0],1)
 def test_renew_transfer_rollback(self):
  self.c.commit()
  try:
   with self.c:app.action(self.c,self.u,'renew',{'child':self.k,'group':self.gs[1]['id'],'credits':-1})
  except app.Problem:pass
  self.assertEqual(self.c.execute('SELECT group_id FROM memberships WHERE ends IS NULL').fetchone()[0],self.gs[0]['id']);self.assertEqual(self.c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],0)
 def test_parent_sees_only_linked_children(self):
  app.action(self.c,self.u,'user',{'name':'ولي أمر','email':'parent@test.local','password':'test-password-123456','role':'parent'})
  parent=dict(self.c.execute("SELECT * FROM users WHERE role='parent'").fetchone());self.assertEqual(app.snapshot(self.c,parent)['children'],[])
  self.c.execute('UPDATE children SET parent=? WHERE id=?',(parent['id'],self.k));self.assertEqual(len(app.snapshot(self.c,parent)['children']),1)
 def test_expired_subscription_blocks_attendance(self):
  self.c.execute('UPDATE subscriptions SET expires=?',((date.today()-timedelta(days=1)).isoformat(),))
  with self.assertRaises(app.Problem):app.action(self.c,self.u,'attendance',{'child':self.k,'status':'present'})
 def new_user(self,role,branch='b1',**extra):
  email=app.uid().lower()+'@test.local';app.action(self.c,self.u,'user',{'name':role,'email':email,'password':'test-password-123456','role':role,'branch':branch,**extra});return dict(self.c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone())
 def tariff(self,branch='b1',amount=700):
  app.action(self.c,self.u,'tariff',{'branch':branch,'sport':'سباحة','level':'','name':'8 حصص','credits':8,'duration':30,'amount':amount});return self.c.execute('SELECT id FROM tariffs ORDER BY rowid DESC LIMIT 1').fetchone()[0]
 def test_finance_is_branch_scoped(self):
  finance=self.new_user('finance','b2');snap=app.snapshot(self.c,finance)
  self.assertEqual([b['id'] for b in snap['branches']],['b2']);self.assertEqual(snap['children'],[]);self.assertEqual(snap['staff'],[]);self.assertEqual(snap['coaches'],[]);self.assertEqual(snap['accounts'],[])
  with self.assertRaises(app.Problem):app.action(self.c,finance,'renew',{'child':self.k,'credits':8})
  with self.assertRaises(app.Problem):app.action(self.c,finance,'staff_log',{'staff':self.coach,'status':'present'})
 def test_student_can_only_read_own_record(self):
  student=self.new_user('student',child=self.k);other=self.register('عمر محمد خالد');snap=app.snapshot(self.c,student)
  self.assertEqual([k['id'] for k in snap['children']],[self.k]);self.assertEqual(snap['accounts'],[])
  with self.assertRaises(app.Problem):app.child(self.c,student,other)
  with self.assertRaises(app.Problem):app.action(self.c,student,'attendance',{'child':self.k,'status':'present'})
 def test_future_subscription_cannot_record_early_attendance(self):
  self.c.execute('UPDATE subscriptions SET starts=?',((date.today()+timedelta(days=3)).isoformat(),))
  with self.assertRaises(app.Problem):app.action(self.c,self.u,'attendance',{'child':self.k,'status':'present'})
 def test_bad_subscription_dates_roll_back_registration(self):
  self.c.commit();before=self.c.execute('SELECT COUNT(*) FROM children').fetchone()[0]
  with self.assertRaises(app.Problem):
   with self.c:app.action(self.c,self.u,'register',{'name':'اختبار محمد أحمد','dob':'2018-01-01','phone':'01000000000','group':self.gs[0]['id'],'starts':(date.today()+timedelta(days=3)).isoformat(),'expires':date.today().isoformat()})
  self.assertEqual(self.c.execute('SELECT COUNT(*) FROM children').fetchone()[0],before)
 def test_future_renewal_preserves_current_balance_and_original_dates(self):
  old=dict(self.c.execute('SELECT * FROM subscriptions').fetchone());app.action(self.c,self.u,'renew',{'child':self.k,'starts':(date.today()+timedelta(days=5)).isoformat(),'expires':(date.today()+timedelta(days=35)).isoformat(),'credits':10,'amount':1000})
  app.action(self.c,self.u,'attendance',{'child':self.k,'status':'present'});plans=app.rows(self.c,'SELECT * FROM subscriptions ORDER BY rowid');self.assertEqual(plans[0]['expires'],old['expires']);self.assertEqual(plans[0]['remaining'],7);self.assertEqual(plans[1]['remaining'],10)
 def test_price_is_derived_on_server_and_cannot_use_other_branch(self):
  tariff=self.tariff();reception=self.new_user('reception');app.action(self.c,reception,'renew',{'child':self.k,'tariff':tariff,'amount':1,'credits':999})
  p=self.c.execute('SELECT * FROM subscriptions ORDER BY rowid DESC LIMIT 1').fetchone();self.assertEqual(p['amount'],700);self.assertEqual(p['credits'],8)
  other=self.tariff('b2')
  with self.assertRaises(app.Problem):app.action(self.c,reception,'renew',{'child':self.k,'tariff':other})
 def test_tariff_revision_does_not_change_previous_subscription(self):
  t=self.tariff();app.action(self.c,self.u,'renew',{'child':self.k,'tariff':t});app.action(self.c,self.u,'tariff',{'tariff':t,'branch':'b1','sport':'سباحة','level':'','name':'new','credits':8,'duration':30,'amount':1200});self.assertEqual(self.c.execute('SELECT amount FROM subscriptions ORDER BY rowid DESC LIMIT 1').fetchone()[0],700);self.assertEqual(self.c.execute('SELECT enabled FROM tariffs WHERE id=?',(t,)).fetchone()[0],0)
 def test_account_update_revokes_sessions_and_self_disable_is_blocked(self):
  user=self.new_user('reception');app.insert(self.c,'sessions',token='session-test',user_id=user['id'],expires=9999999999);app.action(self.c,self.u,'account_update',{'account':user['id'],'enabled':0});self.assertEqual(self.c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(user['id'],)).fetchone()[0],0)
  with self.assertRaises(app.Problem):app.action(self.c,self.u,'account_update',{'account':self.u['id'],'enabled':0})
 def test_edit_group_blocks_coach_collision(self):
  g=self.gs[1]
  with self.assertRaises(app.Problem):app.action(self.c,self.u,'group_update',{'group_id':g['id'],'name':'edited','coach':self.coach,'start':'16:30','end':'17:30','days':[date.today().weekday()],'capacity':5})
 def test_student_account_follows_child_transfer(self):
  student=self.new_user('student',child=self.k);app.action(self.c,self.u,'transfer',{'child':self.k,'group':self.gs[1]['id'],'reason':'اختبار'});snap=app.snapshot(self.c,student);self.assertEqual(snap['children'][0]['id'],self.k);self.assertEqual([m for m in snap['memberships'] if not m['ends']][0]['group_id'],self.gs[1]['id'])
if __name__=='__main__':unittest.main()
