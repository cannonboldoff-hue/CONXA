# WF-17: isolated Conxa install (own CONXA_DIR + data dir) with hand-built skills for the wf17_authsite.py fixture.
# usage: wf17_install.py <root> [--sessions]   (--sessions: log in to the fixture via HTTP and write raw saved sign-ins)
# Group "wf17-fixture" has two apps (App A = 127.0.0.1:8101, App B = localhost:8101). No product code is touched.
import json, shutil, sys, os, subprocess, hashlib, urllib.request, http.cookiejar
root = sys.argv[1]; H = os.path.expanduser('~/.conxa-build-studio-dev')
sb = H + '/sandbox/.conxa'; tpl = H + '/data/skill-packs/wrk_local/e2e-signin4-2026-11-16-56941ff2/wf1-b-secure-area-logout-r4-ad722abe'
cd = root + '/conxa'; os.makedirs(cd + '/conxa-app', exist_ok=True); os.makedirs(root + '/data', exist_ok=True)
for f in ('conxa-runtime.exe', 'keytar.node', 'version.json'): shutil.copy(sb + '/' + f, cd + '/' + f)
app = [d for d in os.listdir(H + '/deps/conxa-app') if d.startswith('app-')][0]
if not os.path.exists(cd + '/conxa-app/' + app): shutil.copytree(H + '/deps/conxa-app/' + app, cd + '/conxa-app/' + app)
def junction(link, target):
    if not os.path.exists(link): subprocess.run(['cmd', '/c', 'mklink', '/J', link.replace('/', chr(92)), target.replace('/', chr(92))], check=True, stdout=subprocess.DEVNULL)
junction(cd + '/conxa-app/current', cd + '/conxa-app/' + app); junction(cd + '/chromium', H + '/deps/chromium')

GID = 'f17f17f1-0000-4000-8000-000000000017'; GS = 'wf17-fixture'
A = 'http://127.0.0.1:8101'; B = 'http://localhost:8101'
TAB = {'id': 'tab_0', 'index': 0, 'opened_by': 'initial', 'opener_tab': None}
EMPTY_FP = {k: '' for k in ('role', 'tag', 'inner_text', 'aria_label', 'name', 'placeholder', 'label_text', 'alt', 'title', 'data_testid', 'input_type')}
EMPTY_FP.update({'css_class_tokens': [], 'anchor_phrases': [], 'position_hint': {}})
HINTS = {'hover_chain': [], 'virtualized_container': '', 'allow_forced_action': False, 'repeat_count': 1, 'control_kind': '', 'date_picker': {}, 'choice': {}}
def nav(url, label):
    return {'type': 'navigate', 'url': url, 'tab': TAB, 'identity_bundle': {'signals': [], 'fingerprint': EMPTY_FP, 'stable_hash': '', 'frame_chain': [], 'shadow_path': [], 'compat_fingerprint': '', 'guid_like_attrs': [], 'destructive': False},
            'handler_hints': HINTS, 'label': label, 'source_step_key': hashlib.sha1(('n' + url + label).encode()).hexdigest()[:16] + '#1'}
def click(name, label, asserts, tag='a', role='link'):
    fp = dict(EMPTY_FP, role=role, tag=tag, inner_text=name)
    sigs = [{'engine': 'role', 'selector': f'internal:role={role}[name="{name}"]', 'durability': 0.95, 'orthogonality_class': 'semantic-aria', 'unique_at_compile': True, 'source': 'compiler'},
            {'engine': 'text_based', 'selector': f'internal:text="{name}"', 'durability': 0.85, 'orthogonality_class': 'visible-text', 'unique_at_compile': True, 'source': 'compiler'}]
    h = hashlib.sha256(name.encode()).hexdigest()
    return {'type': 'click', 'selector': f'role={role}[name="{name}"]', 'tab': TAB, 'identity_bundle': {'signals': sigs, 'fingerprint': fp, 'stable_hash': h, 'frame_chain': [], 'shadow_path': [], 'compat_fingerprint': '', 'guid_like_attrs': [], 'destructive': False},
            'handler_hints': HINTS, 'validation': {'assertions': asserts}, 'label': label, 'source_step_key': h + '#1'}
def text(t): return {'type': 'text_present', 'target': t, 'required': True}
SK = {
 'wf17-two-apps': (A + '/app', [nav(A + '/app', 'Open App A'), click('Go to the other app', 'Go to App B', [text('App B home')]), click('Open reports', 'Open reports in App B', [text('App B reports loaded')])]),
 'wf17-a-only': (A + '/app', [nav(A + '/app', 'Open App A'), click('Open reports', 'Open reports in App A', [text('App A reports loaded')])]),
 'wf17-b-only': (B + '/app', [nav(B + '/app', 'Open App B'), click('Open reports', 'Open reports in App B', [text('App B reports loaded')])]),
 'wf17-slow': (A + '/app', [nav(A + '/app', 'Open App A'), click('Open slow reports', 'Open slow reports in App A', [text('App A reports loaded')])]),
 'wf17-signout': (A + '/app', [nav(A + '/app', 'Open App A'), click('Sign out', 'Sign out of App A', [text('App A sign in')])]),
 'wf17-visit-login': (A + '/login', [nav(A + '/login', 'Open the sign-in page'), click('Skip, go to the app', 'Skip to the app', [text('App A home')])]),
}
pd = cd + '/skill-packs/wrk_local'; os.makedirs(pd, exist_ok=True); names = []; grp = {}
for slug, (target, steps) in SK.items():
    v = f'{pd}/{GS}/{slug}/v0.1.0'; os.makedirs(v, exist_ok=True)
    json.dump(steps, open(v + '/execution.json', 'w'), indent=2); shutil.copy(tpl + '/recovery.json', v + '/recovery.json'); shutil.copy(tpl + '/inputs.json', v + '/inputs.json')
    m = json.load(open(tpl + '/manifest.json')); m.update({'slug': slug, 'name': slug, 'description': slug, 'target_url': target, 'group_id': GID, 'structural_fingerprint': {'landmarks': [], 'landmark_count': 0}})
    m['checksum'] = {f: hashlib.sha256(open(v + '/' + f, 'rb').read()).hexdigest() for f in ('execution.json', 'recovery.json', 'inputs.json')}
    json.dump(m, open(v + '/manifest.json', 'w'), indent=2); junction(f'{pd}/{GS}/{slug}/current', v); names.append(slug); grp[slug] = GS
def appdef(i, name, base):
    return {'id': i, 'name': name, 'login_url': base + '/login', 'success_url': base + '/app', 'person_confirmed': False,
            'auth_definition': {'version': 1, 'probe_url': base + '/app', 'signed_out': {'final_path': '/login', 'markers': [{'role': 'button', 'name': 'Sign in'}], 'password_box': True},
                                'signed_in': {'final_path': '/app', 'endpoints': []}, 'session_keys': [], 'journey_hosts': [base.split('//')[1].split(':')[0]]}}
p = json.load(open(H + '/data/skill-packs/wrk_local/pack.json'))
p.update({'skills': names, 'skill_groups': grp, 'groups': [{'id': GID, 'slug': GS, 'name': 'WF17 fixture', 'apps': [appdef('app-a', 'App A', A), appdef('app-b', 'App B', B)]}], 'target_url': A + '/app'})
p['tracking']['tracking_url'] = p['tracking']['tracking_url'].replace('wrk_local', 'wf17_ws'); p['tracking']['workspace_id'] = 'wf17_ws'
json.dump(p, open(pd + '/pack.json', 'w'), indent=2)
if '--sessions' in sys.argv:
    ss = root + '/data/cache/sessions'; os.makedirs(ss, exist_ok=True)
    for i, base in (('app-a', A), ('app-b', B)):
        cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        op.open(base + '/login', data=b'user=demo&pw=pass123').read()
        ck = [{'name': c.name, 'value': c.value, 'domain': base.split('//')[1].split(':')[0], 'path': '/', 'expires': -1, 'httpOnly': False, 'secure': False, 'sameSite': 'Lax'} for c in cj]
        assert any(c['name'] == 'sid' for c in ck), 'login failed'
        json.dump({'cookies': ck, 'origins': []}, open(f'{ss}/wrk_local__{i}_raw_state.json', 'w'))
print('ok', names, app)
