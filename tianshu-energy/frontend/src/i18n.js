import i18n from './art/locales';import zh from './locales/zh.json';import en from './locales/en.json';
export const locale=i18n.global.locale;
const keys=new Map(Object.entries(zh.messages||{}).map(([key,value])=>[value,key]));
export function tr(value){if(typeof value!=='string')return value;const key=keys.get(value);if(locale.value==='zh')return value;if(key)return en.messages[key]||value;for(const [raw,k] of keys){if(raw.endsWith('：')&&value.startsWith(raw))return (en.messages[k]||raw)+value.slice(raw.length);}return value;}
export function changeLanguage(value){if(!['zh','en'].includes(value))return;locale.value=value;document.documentElement.lang=value==='en'?'en':'zh-CN';localStorage.setItem('energy-ui-language',JSON.stringify(value));}

export function bandName(value){return locale.value==='en'?({'谷':'Off-peak','平':'Standard','峰':'Peak','尖':'Critical peak'}[value]||value):value;}
export function detailText(value){
 if(locale.value!=='en'||typeof value!=='string')return value;
 const exact=tr(value);if(exact!==value)return exact;
 const patterns=[
 [/^(.+) 当前 ([\d.]+) kW，阈值 ([\d.]+) kW$/,(_,name,p,limit)=>`${name}: ${p} kW; limit ${limit} kW`],
 [/^(.+) 已超过 10 秒没有上报；当前功率未知$/,(_,name)=>`${name}: no report for over 10 seconds; current power unknown`],
 [/^(.+) 上报电压 ([\d.]+) V，请现场核实$/,(_,name,v)=>`${name}: reported voltage ${v} V; verify on site`],
 [/^(.+) 最近剩余 (.+) 度，读数按第三方查询计划更新$/,(_,name,b)=>`${name}: last balance ${b} kWh; updated on the API query schedule`],
 [/^(.+) 最近剩余 (.+) 度，请核实并及时充值$/,(_,name,b)=>`${name}: last balance ${b} kWh; verify and top up promptly`],
 [/^(.+) 预计可用约 (.+) 小时，属于本地保守估计$/,(_,name,h)=>`${name}: about ${h} hours remaining; conservative local estimate`],
 [/^预计 ¥(.+)，预算 ¥(.+)。这是当前电价和历史用量下的参考估算。$/,(_,f,b)=>`Forecast ¥${f}; budget ¥${b}. Reference estimate based on current tariffs and recorded usage.`],
 [/^(.+) 最近上报功率超过设备设置阈值，请核实$/,(_,name)=>`${name}: reported power exceeded the configured threshold; please verify`]
 ];
 for(const [pattern,render] of patterns){const match=value.match(pattern);if(match)return render(...match);}
 return value;
}
