"""Local UI preferences: validated, isolated by account, independent of launch port."""
import sys,unittest,tempfile,json,threading,http.client
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from energy import Energy,Error
from server import Server
BASE=ROOT/'build/preference-tests';BASE.mkdir(parents=True,exist_ok=True)
USER={'id':'viewer-test','username':'test-viewer','role':'viewer'}
PREF={'language':'en','mode':'dark','primary':'#B48DF3','radius':'0.5','border':True}
class Preferences(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=BASE);self.addCleanup(self.tmp.cleanup);self.app=Energy(self.tmp.name,seed=False);self.addCleanup(lambda:self.app.close())
 def read(self,user=USER):return self.app.dispatch('GET','/api/preferences',user,{}, {})['preferences']
 def save(self,p=PREF,user=USER):return self.app.dispatch('POST','/api/preferences',user,p,{})
 def test_viewer_can_save_own_appearance_without_control_permission(self):
  self.assertIsNone(self.read());self.save();self.assertEqual(self.read(),PREF);self.assertFalse(self.app.rows('SELECT * FROM commands'))
 def test_accounts_and_demo_have_separate_preferences(self):
  self.save();self.assertIsNone(self.read({'id':'other','role':'admin'}));self.assertIsNone(self.read({'id':'demo','role':'demo'}));self.save({**PREF,'mode':'light'},user={'id':'other','role':'admin'});self.assertEqual(self.read()['mode'],'dark')
 def test_invalid_css_language_and_values_never_replace_valid_settings(self):
  self.save()
  for bad in [{'primary':'red;display:none'},{'language':'unknown'},{'mode':'javascript'},{'radius':'1000'},{'border':'false'}]:
   with self.assertRaises(Error):self.save({**PREF,**bad})
   self.assertEqual(self.read(),PREF)
 def test_preferences_survive_database_restart(self):
  self.save();self.app.close();self.app=Energy(self.tmp.name,seed=False);self.assertEqual(self.read(),PREF)
 @unittest.skipUnless('--http' in sys.argv,'use --http for localhost checks')
 def test_new_launch_port_uses_saved_preferences_and_requires_csrf(self):
  sid,session=self.app.make_session(USER)
  def request(server,method,body=None,csrf=None,cookie=True):
   c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5);headers={'Content-Type':'application/json','Cookie':'tianshu_session='+sid if cookie else '','X-CSRF-Token':session['csrf'] if csrf is None else csrf};c.request(method,'/api/preferences',json.dumps(body) if body is not None else None,headers);r=c.getresponse();out=(r.status,json.loads(r.read()));c.close();return out
  for index in range(2):
   s=Server(('127.0.0.1',0),self.app,ROOT/'web');t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
   try:
    if index==0:self.assertEqual(request(s,'POST',PREF)[0],200)
    else:self.assertEqual(request(s,'GET')[1]['preferences'],PREF)
    self.assertEqual(request(s,'GET',cookie=False)[0],401);self.assertEqual(request(s,'POST',PREF,csrf='bad')[0],403)
   finally:s.shutdown();s.server_close();t.join()
if __name__=='__main__':
 http_enabled='--http' in sys.argv
 if http_enabled:sys.argv.remove('--http')
 # unittest's skip condition is evaluated above, before flag removal.
 unittest.main(verbosity=2)
