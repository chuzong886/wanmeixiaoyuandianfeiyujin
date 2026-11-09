from pathlib import Path
import json,shutil
root=Path('tianshu-energy');modules=root/'frontend/node_modules'
packages=['@intlify/core-base','@intlify/shared','@intlify/message-compiler','@vueuse/shared','vue','vue-router','vue-i18n','pinia','pinia-plugin-persistedstate','element-plus','@iconify/vue','@iconify-json/ri','@vueuse/core','@vue/runtime-core','@vue/runtime-dom','@vue/reactivity','@vue/shared','echarts','zrender','tslib','vite','@vitejs/plugin-vue']
for name in packages:
 folder=modules/name
 if folder.exists():
  for p in folder.iterdir():
   if p.is_file() and any(w in p.name.lower() for w in ['license','copying','notice']):
    dest=root/'licenses/web'/name.replace('/','-');dest.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest/p.name)
assets=json.loads((root/'launcher/obj/project.assets.json').read_text(encoding='utf-8'));cache=Path.home()/'.nuget/packages'
for name,entry in assets['libraries'].items():
 if entry.get('type')!='package':continue
 folder=cache/entry.get('path',name.lower())
 if not folder.exists():continue
 for p in folder.iterdir():
  if p.is_file() and any(w in p.name.lower() for w in ['license','notice']) and p.suffix.lower() in ['.txt','.md','']:
   dest=root/'licenses/windows'/name.replace('/','-');dest.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest/p.name)
print('Bundled component license texts collected')
