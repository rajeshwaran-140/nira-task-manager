// NIRA service worker: caches only the app shell. API calls are never cached.
const V="nira-v2",SHELL=["/","/icon-192.png","/manifest.json"];
self.addEventListener("install",e=>{e.waitUntil(caches.open(V).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting()))});
self.addEventListener("activate",e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==V).map(x=>caches.delete(x)))).then(()=>self.clients.claim()))});
self.addEventListener("fetch",e=>{
  const r=e.request,u=new URL(r.url);
  if(r.method!=="GET"||u.origin!==location.origin||!((r.mode==="navigate"&&u.pathname==="/")||SHELL.includes(u.pathname)))return; // only the app shell is cached; every API call (/assistant, /deadlines, /auth, /me, /settings ...) and every other page goes straight to the network
  e.respondWith(fetch(r).then(res=>{const cp=res.clone();caches.open(V).then(c=>c.put(r.mode==="navigate"?"/":r,cp));return res}).catch(()=>caches.match(r.mode==="navigate"?"/":r)));
});
