'use strict';
const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
if(!reducedMotion&&'IntersectionObserver' in window){const observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){entry.target.classList.add('is-visible');observer.unobserve(entry.target);}},{threshold:.06});document.querySelectorAll('.reveal').forEach(el=>{el.classList.add('will-reveal');observer.observe(el);});}
document.querySelectorAll('details').forEach(el=>el.addEventListener('toggle',()=>{if(el.open&&el.closest('.faq-list'))el.closest('.faq-list').querySelectorAll('details').forEach(other=>{if(other!==el)other.open=false;});}));
