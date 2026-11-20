import json,sys
a=json.load(open(sys.argv[1]));b=json.load(open(sys.argv[2]))
def diff(x,y,p=''):
    if isinstance(x,dict) and isinstance(y,dict):
        for k in sorted(set(x)|set(y)): diff(x.get(k),y.get(k),p+'/'+k)
    elif isinstance(x,list) and isinstance(y,list) and len(x)==len(y):
        for i,(u,v) in enumerate(zip(x,y)): diff(u,v,p+f'[{i}]')
    elif x!=y: print(p,'|',str(x)[:90],'->',str(y)[:90])
diff(a,b); print('(end diff)')
