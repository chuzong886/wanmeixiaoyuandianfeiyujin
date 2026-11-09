import {ref,watch,onBeforeUnmount} from 'vue';import {api} from './api';import {useSettingStore} from './art/store/modules/setting';import {useTheme} from './art/hooks/core/useTheme';import {locale,changeLanguage} from './i18n';
export function usePreferences(user){
 const setting=useSettingStore(),theme=useTheme(),status=ref(''),loading=ref(true);let timer,revision=0;
 const payload=()=>({language:locale.value,mode:setting.systemThemeMode,primary:setting.systemThemeColor,radius:String(setting.customRadius),border:setting.boxBorderMode});
 function cancel(){clearTimeout(timer);revision++;loading.value=true;}
 async function load(){cancel();const run=revision;status.value='';try{const {preferences:p}=await api('preferences');if(run!==revision)return;if(p){changeLanguage(p.language);setting.setElementTheme(p.primary);theme.switchThemeStyles(p.mode);setting.setCustomRadius(p.radius);if(setting.boxBorderMode!==p.border)setting.setBorderMode();}status.value='偏好已保存到本机';}catch(e){status.value='偏好读取失败：'+e.message;}finally{if(run===revision)loading.value=false;}}
 watch(()=>JSON.stringify(payload()),()=>{if(loading.value||!user())return;clearTimeout(timer);status.value='正在保存偏好…';const run=revision;timer=setTimeout(async()=>{try{await api('preferences',payload());if(run===revision)status.value='偏好已保存到本机';}catch(e){if(run===revision)status.value='偏好保存失败：'+e.message;}},250);});
 onBeforeUnmount(cancel);return {status,load,cancel};
}
