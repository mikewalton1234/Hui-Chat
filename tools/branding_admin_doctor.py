#!/usr/bin/env python3
from pathlib import Path
import json
ROOT = Path(__file__).resolve().parents[1]
checks=[]
def check(name, ok):
    checks.append((name,bool(ok)))
    print(('PASS' if ok else 'FAIL')+' '+name)
chat=(ROOT/'templates/chat.html').read_text(encoding='utf-8')
login=(ROOT/'templates/login.html').read_text(encoding='utf-8')
admin=(ROOT/'admin_panel_inject.py').read_text(encoding='utf-8')
routes=(ROOT/'routes_admin_tools.py').read_text(encoding='utf-8')
config_path = ROOT/'server_config.json'
if not config_path.exists():
    config_path = ROOT/'server_config.example.json'
config=json.loads(config_path.read_text(encoding='utf-8'))
check('branding helper exists',(ROOT/'branding.py').is_file())
check('chat logo conditional','branding.logo_enabled' in chat)
check('loading screen conditional','branding.loading_screen_enabled' in chat)
check('text loading mode',"branding.loading_screen_mode == 'text'" in chat)
check('login logo conditional',login.count('branding.logo_enabled') == 2)
check('admin branding API','/admin/settings/branding' in routes)
check('admin logo upload API','branding/upload/<asset_kind>' in routes)
check('admin branding controls','ecapBrandLogoEnabled' in admin and 'ecapBrandLoadingMode' in admin)
if config_path.name == 'server_config.json':
    check('ten instances preserved',config.get('production_instance_count') == 10)
else:
    check('packaged example uses safe instance count',config.get('production_instance_count') == 1)
check('branding defaults enabled',config.get('branding_logo_enabled') is True and config.get('branding_loading_screen_enabled') is True)
failed=sum(1 for _,ok in checks if not ok)
print(f'\n{len(checks)-failed} passed, {failed} failed')
raise SystemExit(1 if failed else 0)
