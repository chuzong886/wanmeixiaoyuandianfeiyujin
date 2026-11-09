import {createApp} from 'vue';import {addCollection} from '@iconify/vue';import ri from '@iconify-json/ri/icons.json';
import ElementPlus from 'element-plus';import 'element-plus/dist/index.css';import 'element-plus/theme-chalk/dark/css-vars.css';
import {initStore} from './art/store';import {router} from './art/router';import i18n from './art/locales';import App from './App.vue';
import './art/assets/styles/core/tailwind.css';import './art/assets/styles/index.scss';import './energy-v1.scss';
import {tr} from './i18n';addCollection(ri);const app=createApp(App);app.config.globalProperties.tr=tr;initStore(app);app.use(router).use(i18n).use(ElementPlus).mount('#app');
