import {createI18n} from 'vue-i18n'
import zh from './precompiled/zh';import en from './precompiled/en'
let saved='zh';try{saved=JSON.parse(localStorage.getItem('energy-ui-language')||'"zh"')}catch{}
const i18n=createI18n({legacy:false,locale:saved==='en'?'en':'zh',fallbackLocale:'zh',globalInjection:true,messages:{zh,en}})
export const $t=(key:string)=>i18n.global.t(key)
export default i18n
export const languageOptions=[{value:'zh',label:'简体中文'},{value:'en',label:'English'}]
