import json,sys
for l in open(sys.argv[1],encoding='utf-8'):
    d=json.loads(l); k=d.get('kind') or d.get('op')
    print(k,str(d.get('id',''))[:8],str(d.get('text') or d.get('ids') or d.get('card_type') or '')[:55].replace('\n',' '),d.get('replaces',''))
