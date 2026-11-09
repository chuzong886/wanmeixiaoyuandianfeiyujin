// Compile official framework messages at build time. Browser CSP remains script-src 'self'.
import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';import {baseCompile} from '@intlify/message-compiler';
const root=path.dirname(fileURLToPath(import.meta.url)),out=path.join(root,'src/art/locales/precompiled');fs.mkdirSync(out,{recursive:true});
function encode(value,key){
 if(typeof value==='string')return baseCompile(value,{mode:'arrow',onError:error=>{throw Error(key+': '+error.message)}}).code;
 if(Array.isArray(value))return '['+value.map((v,i)=>encode(v,key+'.'+i)).join(',')+']';
 if(value&&typeof value==='object')return '{'+Object.entries(value).map(([k,v])=>JSON.stringify(k)+':'+encode(v,key+'.'+k)).join(',')+'}';
 return JSON.stringify(value);
}
for(const language of ['zh','en']){
 const official=JSON.parse(fs.readFileSync(path.join(root,'src/art/locales/langs/'+language+'.json'),'utf8'));
 const energy=JSON.parse(fs.readFileSync(path.join(root,'src/locales/'+language+'.json'),'utf8'));
 fs.writeFileSync(path.join(out,language+'.js'),'// Generated from upstream and energy locale resources.\nexport default '+encode({...official,energy:{nav:energy.nav}},language)+';\n');
}
console.log('Locale resources compiled; browser runtime does not evaluate source strings');
