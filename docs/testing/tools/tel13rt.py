# Dump telemetry events for the workflow "wf13-marie-curie-rt" since a UTC epoch (default last 3 min). usage: tel13rt.py [sinceEpochSec] [workflowIdSubstr]
import json,glob,os,sys,time,collections
since=float(sys.argv[1]) if len(sys.argv)>1 else time.time()-180
wf=sys.argv[2] if len(sys.argv)>2 else 'wf13-marie-curie-rt'
d='C:/Users/kiran/.conxa-build-studio-dev/data/kv/tracking/wrk_local/'
ev=[]
for f in glob.glob(d+'*.json'):
    if os.path.getmtime(f)<since-5: continue
    try: arr=json.load(open(f))
    except Exception: continue
    for b in arr:
        if wf in b.get('workflow_id','') and b.get('server_ts',0)>=since:
            for e in b['events']: ev.append((e.get('ts',0),b['run_id'],e))
ev.sort(key=lambda x:x[0])
c=collections.Counter()
for ts,r,e in ev:
    c[e['e']]+=1
    print(time.strftime('%H:%M:%S',time.gmtime(ts/1000)),r[-5:],json.dumps(e)[:230])
print(dict(c))
