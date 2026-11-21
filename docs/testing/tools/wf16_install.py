# Build an isolated Conxa install (CONXA_DIR + data dir) from Studio's built skills. usage: wf16_install.py <root>
# Copies exe/app layer/skills, makes junctions, writes pack.json, adds a safe-decision variant skill.
import json,shutil,sys,os,subprocess,hashlib
root=sys.argv[1]; H=os.path.expanduser('~/.conxa-build-studio-dev')
sb=H+'/sandbox/.conxa'; packs=H+'/data/skill-packs/wrk_local'
cd=root+'/conxa'; os.makedirs(cd+'/conxa-app',exist_ok=True); os.makedirs(root+'/data',exist_ok=True)
for f in ('conxa-runtime.exe','keytar.node','version.json'): shutil.copy(sb+'/'+f,cd+'/'+f)
app=[d for d in os.listdir(sb+'/conxa-app') if d.startswith('app-')][0]
if not os.path.exists(cd+'/conxa-app/'+app): shutil.copytree(sb+'/conxa-app/'+app,cd+'/conxa-app/'+app)
def junction(link,target):
    if not os.path.exists(link): subprocess.run(['cmd','/c','mklink','/J',link.replace('/',chr(92)),target.replace('/',chr(92))],check=True,stdout=subprocess.DEVNULL)
junction(cd+'/conxa-app/current',cd+'/conxa-app/'+app); junction(cd+'/chromium',H+'/deps/chromium')
skills={'probe-group-08501f47':['wf4-d-i18n-wiki-r5-06fd6288','mutator-fixture-72c45380','wf13-marie-curie-rt-283e911a','wf10-decision-r3-94560d1d','wf10-branches-r3-16be31f3','wf11-note-r1-eafa1f0a','dismiss-df-2d7cebf8'],
        'e2e-2026-11-16-19291d9d':['wf1-a-wikipedia-control-r4-71600c18']}
pd=cd+'/skill-packs/wrk_local'; os.makedirs(pd,exist_ok=True); names=[]; grp={}
for g,ss in skills.items():
    for s in ss:
        shutil.copytree(f'{packs}/{g}/{s}',f'{pd}/{g}/{s}/v0.1.0',dirs_exist_ok=True); junction(f'{pd}/{g}/{s}/current',f'{pd}/{g}/{s}/v0.1.0'); names.append(s); grp[s]=g
# variant: decision popup, step NOT marked irreversible
src=f'{pd}/probe-group-08501f47/wf10-decision-r3-94560d1d/v0.1.0'; vb=f'{pd}/probe-group-08501f47/wf16-decision-safe'; v=vb+'/v0.1.0'
shutil.copytree(src,v,dirs_exist_ok=True); junction(vb+'/current',v)
e=json.load(open(v+'/execution.json'))
for s in e: s['destructive']=False; s.pop('consequence','_')
json.dump(e,open(v+'/execution.json','w'),indent=2)
m=json.load(open(v+'/manifest.json')); m['checksum']={f:hashlib.sha256(open(v+'/'+f,'rb').read()).hexdigest() for f in m['checksum']}; m['slug']='wf16-decision-safe'; m['name']='WF16 decision safe'
json.dump(m,open(v+'/manifest.json','w'),indent=2); names.append('wf16-decision-safe')
p=json.load(open(packs+'/pack.json'))
grp['wf16-decision-safe']='probe-group-08501f47'; p['skill_groups']=grp; p['skills']=names

# fixture-site skills get their own group (host 127.0.0.1 only) so they can overlap with wikipedia runs
FX='wf16-fixtures'; FXID='f16f16f1-0000-4000-8000-000000000016'
p['groups'].append({'id':FXID,'slug':FX,'name':'WF16 fixtures'})
for sl in ('mutator-fixture-72c45380','wf10-decision-r3-94560d1d','wf16-decision-safe','wf11-note-r1-eafa1f0a','dismiss-df-2d7cebf8'):
    src=f'{pd}/probe-group-08501f47/{sl}'; dst=f'{pd}/{FX}/{sl}'
    os.makedirs(dst,exist_ok=True); shutil.copytree(src+'/v0.1.0',dst+'/v0.1.0',dirs_exist_ok=True); junction(dst+'/current',dst+'/v0.1.0')
    mf=dst+'/v0.1.0/manifest.json'; m=json.load(open(mf)); m['group_id']=FXID; json.dump(m,open(mf,'w'),indent=2); grp[sl]=FX

# loop variant: navigate (popup shows after 1 s) then for_each(items) whose body is the same non-irreversible click
lb=f'{pd}/{FX}/wf16-decision-loop'; lv=lb+'/v0.1.0'
shutil.copytree(f'{pd}/{FX}/wf16-decision-safe/v0.1.0',lv,dirs_exist_ok=True); junction(lb+'/current',lv)
e=json.load(open(lv+'/execution.json')); e=[e[0],{'type':'for_each','as':'row','items':'items','max_iterations':2,'steps':[e[1]]}]
json.dump(e,open(lv+'/execution.json','w'),indent=2); json.dump({'inputs':[{'name':'items','type':'string','description':'items'}]},open(lv+'/inputs.json','w'))
m=json.load(open(lv+'/manifest.json')); m['slug']='wf16-decision-loop'; m['name']='WF16 decision loop'; m['inputs_required']=['items']
m['checksum']={f:hashlib.sha256(open(lv+'/'+f,'rb').read()).hexdigest() for f in m['checksum']}; json.dump(m,open(lv+'/manifest.json','w'),indent=2)
names.append('wf16-decision-loop'); grp['wf16-decision-loop']=FX

# the decision skills use our own fixture server on :8097 (its request log is the proof of what was clicked)
for sl in ('wf10-decision-r3-94560d1d','wf16-decision-safe','wf16-decision-loop','wf11-note-r1-eafa1f0a','dismiss-df-2d7cebf8'):
    d=f'{pd}/{FX}/{sl}/v0.1.0'
    for f in ('execution.json','recovery.json'):
        t=open(f'{d}/{f}',encoding='utf8').read().replace('127.0.0.1:8099/df.html','127.0.0.1:8099/dismiss-fixture.html?banner=onetrust').replace('127.0.0.1:8099','127.0.0.1:8097'); open(f'{d}/{f}','w',encoding='utf8').write(t)
    m=json.load(open(d+'/manifest.json')); m['target_url']=m['target_url'].replace('8099','8097')
    m['checksum']={f:hashlib.sha256(open(d+'/'+f,'rb').read()).hexdigest() for f in m['checksum']}; json.dump(m,open(d+'/manifest.json','w'),indent=2)

# the popup appears 1 s after load: two 1 s waits make sure it is up before the click (otherwise the click wins the race)
for sl in ('wf10-decision-r3-94560d1d','wf16-decision-safe','wf16-decision-loop'):
    d=f'{pd}/{FX}/{sl}/v0.1.0'; e=json.load(open(d+'/execution.json'))
    w=[{'type':'wait','ms':1000,'tab':e[0].get('tab')} for _ in range(2)]
    e=[e[0]]+w+e[1:]; json.dump(e,open(d+'/execution.json','w'),indent=2)
    m=json.load(open(d+'/manifest.json')); m['checksum']={f:hashlib.sha256(open(d+'/'+f,'rb').read()).hexdigest() for f in m['checksum']}; json.dump(m,open(d+'/manifest.json','w'),indent=2)

# groups claim the hosts their skills visit (so host_lock engages); each app gets an empty saved sign-in (no cookies, no secrets)
APPS={'probe-group-08501f47':[('wikipedia','https://en.wikipedia.org/'),('demoqa','https://demoqa.com/'),('the-internet','https://the-internet.herokuapp.com/')],
      'e2e-2026-11-16-19291d9d':[('wikipedia','https://en.wikipedia.org/')],'wf16-fixtures':[('fixtures','http://127.0.0.1:8099/')]}
ss=root+'/data/cache/sessions'; os.makedirs(ss,exist_ok=True)
for g in p['groups']:
    if g['slug'] in APPS:
        g['apps']=[{'id':i,'name':i,'login_url':u,'success_url':u,'auth_definition':None,'person_confirmed':True} for i,u in APPS[g['slug']]]
        for i,_ in APPS[g['slug']]: json.dump({'cookies':[],'origins':[]},open(f'{ss}/wrk_local__{i}_raw_state.json','w'))
p['tracking']['tracking_url']=p['tracking']['tracking_url'].replace('wrk_local','wf16_ws'); p['tracking']['workspace_id']='wf16_ws'  # own tracking bucket: wrk_local has a stored token, wf16_ws accepts dev events
json.dump(p,open(pd+'/pack.json','w'),indent=2); print('ok',names,app)
