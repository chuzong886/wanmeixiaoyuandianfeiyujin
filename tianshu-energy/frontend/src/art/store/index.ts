import {createPinia} from 'pinia'
import persistedState from 'pinia-plugin-persistedstate'
export const store=createPinia().use(persistedState)
export const initStore=(app:any)=>app.use(store)
