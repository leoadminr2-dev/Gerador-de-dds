/* Public, read-only notice. Never changes authentication, access or child restrictions. */
(function(root){
"use strict";
function minuteInBrasilia(value){
 const parts=new Intl.DateTimeFormat("en-GB",{timeZone:"America/Sao_Paulo",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).formatToParts(new Date(value));
 return Number(parts.find(p=>p.type==="hour").value)*60+Number(parts.find(p=>p.type==="minute").value);
}
function inWindow(value){const m=minuteInBrasilia(value);return m>=180&&m<270;}
function noticeTTL(status){
 if(!status||status.active!==true)return 0;
 const server=Date.parse(status.server_now),expires=Date.parse(status.expires_at);
 if(!Number.isFinite(server)||!Number.isFinite(expires)||!inWindow(server))return 0;
 const remaining=expires-server;
 if(remaining<=0||remaining>600000)return 0;
 const endWindow=(270-minuteInBrasilia(server))*60000-(server%60000);
 return Math.max(0,Math.min(remaining,endWindow));
}
function mount(config){
 if(!config||!config.url||!config.key||!config.slug)return;
 const doc=root.document;
 if(doc.getElementById("app-verification-notice"))return;
 const el=doc.createElement("aside");
 el.id="app-verification-notice";el.hidden=true;
 el.setAttribute("role","status");el.setAttribute("aria-live","polite");
 el.style.cssText="box-sizing:border-box;margin:8px auto;padding:10px 14px;max-width:1100px;border:1px solid #467eac;border-radius:8px;background:#e8f4ff;color:#163b59;font:14px/1.5 system-ui,sans-serif;position:relative;z-index:1";
 doc.body.prepend(el);
 let lastAttempt=0,inFlight=false,expiryTimer=null;
 function hide(){el.hidden=true;el.textContent="";if(expiryTimer)root.clearTimeout(expiryTimer);expiryTimer=null;}
 async function refresh(){
  if(doc.hidden)return;
  const now=Date.now();
  if(!inWindow(now)){hide();return;}
  if(inFlight||(lastAttempt&&now-lastAttempt<300000))return;
  lastAttempt=now;inFlight=true;
  const controller=new AbortController();
  const timeout=root.setTimeout(()=>controller.abort(),8000);
  try{
   const response=await root.fetch(config.url+"/rest/v1/rpc/get_app_verification_status",{
    method:"POST",headers:{"Content-Type":"application/json","apikey":config.key},
    body:JSON.stringify({p_app_slug:config.slug}),signal:controller.signal,cache:"no-store"
   });
   if(!response.ok)throw Error("Status unavailable");
   const data=await response.json();
   const status=Array.isArray(data)?data[0]:null;
   const ttl=noticeTTL(status);
   hide();
   if(ttl>0){
    el.textContent=String(status.message||"Verificação técnica em andamento. Você pode continuar usando o aplicativo.").slice(0,300)+" • Janela: 03h–04h30 (Brasília).";
    el.hidden=false;
    expiryTimer=root.setTimeout(hide,ttl);
   }
  }catch(_){hide();}
  finally{root.clearTimeout(timeout);inFlight=false;}
 }
 doc.addEventListener("visibilitychange",()=>{if(!doc.hidden)void refresh();});
 root.addEventListener("focus",()=>{void refresh();});
 const tick=root.setInterval(()=>{void refresh();},60000);
 void refresh();
 return {refresh,stop:()=>{root.clearInterval(tick);hide();}};
}
const api={mount,noticeTTL,inWindow};
if(typeof module!=="undefined"&&module.exports)module.exports=api;
root.AppVerificationNotice=api;
if(root.document){
 const script=root.document.currentScript;
 const start=()=>{if(script)mount({url:script.dataset.statusUrl,key:script.dataset.statusKey,slug:script.dataset.appSlug});};
 if(root.document.readyState==="loading")root.document.addEventListener("DOMContentLoaded",start,{once:true});else start();
}
})(typeof window!=="undefined"?window:globalThis);
