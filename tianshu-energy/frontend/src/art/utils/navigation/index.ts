import {router} from '@/router'
export const isIframe=(item:any)=>Boolean(item.meta?.isIframe)
export const handleMenuJump=(item:any)=>{if(item.path)router.push(item.path)}
