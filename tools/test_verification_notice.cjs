const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync(require("node:path").join(__dirname,"../web/verification-notice.js"),"utf8");
let checks=0;
async function scenario({clock="2026-10-25T07:05:00Z",status,error=false}={}){
 let fetches=0;
 const elements={},timeouts=new Map();let sequence=0;
 const login={disabled:false};
 const doc={hidden:false,readyState:"complete",currentScript:null,getElementById:id=>elements[id],
 createElement:()=>{const e={hidden:false,textContent:"",style:{},setAttribute(){}};Object.defineProperty(e,"innerHTML",{set(){throw Error("Unsafe HTML");}});return e;},
 body:{prepend:e=>{elements[e.id]=e;}},addEventListener(){}};
 class Clock extends Date{static now(){return new Date(clock).getTime();}}
 const w={document:doc,setTimeout:(f,ms)=>{timeouts.set(++sequence,{f,ms});return sequence;},clearTimeout:id=>timeouts.delete(id),setInterval:()=>1,clearInterval(){},addEventListener(){},fetch:async()=>{fetches++;if(error)throw Error("offline");return {ok:true,json:async()=>[status]};}};
 const context={window:w,Date:Clock,Intl,Number,String,Array,AbortController,console};
 vm.runInNewContext(source,context);
 w.AppVerificationNotice.mount({url:"https://status.example",key:"public",slug:"central"});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(login.disabled,false);
 return {el:elements["app-verification-notice"],fetches,timeouts,api:w.AppVerificationNotice};
}
(async()=>{
 let s=await scenario({clock:"2026-10-25T12:00:00Z"});assert.equal(s.fetches,0);assert.equal(s.el.hidden,true);checks++;
 s=await scenario({status:{active:false}});assert.equal(s.el.hidden,true);checks++;
 const active={active:true,message:"<img src=x onerror=alert(1)>",server_now:"2026-10-25T07:05:00Z",expires_at:"2026-10-25T07:10:00Z"};
 s=await scenario({status:active});assert.equal(s.el.hidden,false);assert.ok(s.el.textContent.startsWith("<img"));checks++;
 const expiry=[...s.timeouts.values()].find(t=>t.ms===300000);assert.ok(expiry);expiry.f();assert.equal(s.el.hidden,true);checks++;
 s=await scenario({status:{...active,expires_at:"2026-10-25T07:04:00Z"}});assert.equal(s.el.hidden,true);checks++;
 s=await scenario({error:true});assert.equal(s.el.hidden,true);checks++;
 assert.equal(s.api.noticeTTL({...active,expires_at:"2026-10-25T08:00:00Z"}),0);checks++;
 assert.equal(s.api.noticeTTL({...active,server_now:"invalid"}),0);checks++;
 assert.equal(s.api.noticeTTL({...active,server_now:"2026-10-25T08:29:30Z",expires_at:"2026-10-25T08:35:00Z"}),30000);checks++;
 assert.equal(s.api.inWindow("2026-10-25T08:30:00Z"),false);checks++;
 for(const [clock,expected] of [["2026-11-05T06:59:59Z",false],["2026-11-05T07:00:00Z",true],["2026-11-05T08:29:59Z",true],["2026-11-05T08:30:00Z",false]]){assert.equal(s.api.inWindow(clock),expected);checks++;}
 s=await scenario({status:active});assert.ok(s.el.textContent.includes("04h–05h30 (Brasília)"));checks++;
 console.log("PASS "+checks+" verification notice scenarios (expiry, time window, safe text, offline, no access blocking)");
})().catch(e=>{console.error(e);process.exitCode=1;});

