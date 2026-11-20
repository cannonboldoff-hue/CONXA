import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
print('version',d['meta'].get('version'),'inputs',[i.get('name') for i in d.get('inputs',[])])
for sk in d['skills']:
  for i,s in enumerate(sk['steps']):
    print(i,s.get('type') or s.get('action'),repr(str(s.get('value'))[:50]),json.dumps({k:v for k,v in s.items() if k.startswith('agent_span')})[:400] if str(s.get('type')or s.get('action')).startswith('agent') else '')
