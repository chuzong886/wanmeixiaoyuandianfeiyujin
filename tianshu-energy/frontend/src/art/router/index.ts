import {createRouter,createWebHashHistory} from 'vue-router'
export const ENERGY_PAGES=['overview','prediction','facilities','meters','cameras','alerts','audit','settings']
export const router=createRouter({history:createWebHashHistory(),routes:[{path:'/',redirect:'/overview'},...ENERGY_PAGES.map(page=>({path:'/'+page,name:page,component:{render:()=>null},meta:{title:'energy.nav.'+page}})),{path:'/:pathMatch(.*)*',redirect:'/overview'}]})
export const HOME_PAGE_PATH='/overview'
