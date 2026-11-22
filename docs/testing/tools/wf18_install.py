# Isolated Conxa install for WF-18 (CONXA_DIR + data dir) built from Studio's built skills. usage: wf18_install.py <root>
# Skills: wf18-handover (as Studio built it), wf18-handover-rw (+ resume_when = box ticked), wf18-loop (hand-over inside for_each),
# mutator-fixture (another skill, SAME host 127.0.0.1, same group -> would wait if host lock were held), wikipedia (other site).
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
pd=cd+'/skill-packs/wrk_local'; os.makedirs(pd,exist_ok=True)
FX='wf18-fixtures'; FXID='f18f18f1-0000-4000-8000-000000000018'; WK='wf18-wiki'; WKID='f18f18f1-0000-4000-8000-000000000019'
def seal(d):
    m=json.load(open(d+'/manifest.json')); m['checksum']={f:hashlib.sha256(open(d+'/'+f,'rb').read()).hexdigest() for f in m['checksum']}; json.dump(m,open(d+'/manifest.json','w'),indent=2)
def put(group,gid,name,src,edit=None,inputs=None):
    d=f'{pd}/{group}/{name}'; v=d+'/v0.1.0'; shutil.copytree(src,v,dirs_exist_ok=True); junction(d+'/current',v)
    m=json.load(open(v+'/manifest.json')); m['group_id']=gid; m['slug']=name; m['name']=name
    if inputs is not None: m['inputs_required']=inputs
    json.dump(m,open(v+'/manifest.json','w'),indent=2)
    if edit:
        e=json.load(open(v+'/execution.json',encoding='utf8')); e=edit(e); json.dump(e,open(v+'/execution.json','w',encoding='utf8'),indent=2,ensure_ascii=False)
    seal(v); return name
base=packs+'/probe-group-08501f47/wf18-handover-r1-001b9c76'
RW={'selector':'#human:checked'}
def rw(e):
    for s in e:
        if s['type']=='handover': s['resume_when']=RW; s['resume_when_timeout_ms']=10000
    return e
def loop(e):
    s=[x for x in e if x['type']=='handover'][0]; s['resume_when']=RW; s['resume_when_timeout_ms']=10000
    c=[x for x in e if x['type']=='click'][0]
    return [e[0],{'type':'for_each','as':'row','items':'items','max_iterations':2,'steps':[s,c]}]
names=[]
names.append(put(FX,FXID,'wf18-handover',base))
names.append(put(FX,FXID,'wf18-handover-rw',base,rw))
names.append(put(FX,FXID,'wf18-loop',base,loop,inputs=['items']))
json.dump({'inputs':[{'name':'items','type':'string','description':'items'}]},open(f'{pd}/{FX}/wf18-loop/v0.1.0/inputs.json','w'))
names.append(put(FX,FXID,'mutator-fixture',packs+'/probe-group-08501f47/mutator-fixture-72c45380'))
names.append(put(WK,WKID,'wf1-a-wikipedia-control',packs+'/e2e-2026-11-16-19291d9d/wf1-a-wikipedia-control-r4-71600c18'))
for n in ('wf18-loop',): seal(f'{pd}/{FX}/{n}/v0.1.0')
p=json.load(open(packs+'/pack.json')); p['skills']=names; p['skill_groups']={n:(FX if n!='wf1-a-wikipedia-control' else WK) for n in names}
p['groups']=[{'id':FXID,'slug':FX,'name':'WF18 fixtures','apps':[{'id':'fixtures','name':'fixtures','login_url':'http://127.0.0.1:8099/','success_url':'http://127.0.0.1:8099/','auth_definition':None,'person_confirmed':True}]},
 {'id':WKID,'slug':WK,'name':'WF18 wiki','apps':[{'id':'wikipedia','name':'wikipedia','login_url':'https://en.wikipedia.org/','success_url':'https://en.wikipedia.org/','auth_definition':None,'person_confirmed':True}]}]
ss=root+'/data/cache/sessions'; os.makedirs(ss,exist_ok=True)
for i in ('fixtures','wikipedia'): json.dump({'cookies':[],'origins':[]},open(f'{ss}/wrk_local__{i}_raw_state.json','w'))
p['tracking']['tracking_url']=p['tracking']['tracking_url'].replace('wrk_local','wf18_ws'); p['tracking']['workspace_id']='wf18_ws'
json.dump(p,open(pd+'/pack.json','w'),indent=2); print('ok',names,app)
