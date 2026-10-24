// Gamepad overlay. Talks to the PC only through window.Bridge (see app.js).
(function(){
const root=document.getElementById('gpView');
const $=s=>root.querySelector(s);
const S={vib:true,str:16,dz:.12,size:1.2,ssize:1.1,theme:'auto'};
try{Object.assign(S,JSON.parse(localStorage.getItem('vgp3')||'{}'))}catch(e){}
const save=()=>{try{localStorage.setItem('vgp3',JSON.stringify(S))}catch(e){}};
const canVib=!!navigator.vibrate;
const vib=m=>{if(S.vib&&canVib)try{navigator.vibrate(m||+S.str)}catch(e){}};
const st={b:{},a:[0,0,0,0]};
const BITS=['A','B','X','Y','L1','R1','L2','R2','Select','Start','L3','R3','Up','Down','Left','Right','Home'];

root.querySelectorAll('[data-b]').forEach(el=>{const n=el.dataset.b;
 el.addEventListener('pointerdown',e=>{e.preventDefault();try{el.setPointerCapture(e.pointerId)}catch(_){}st.b[n]=1;tapMask|=1<<BITS.indexOf(n);flush();el.classList.add('down');vib()});
 const u=()=>{st.b[n]=0;flush();el.classList.remove('down')};
 el.addEventListener('pointerup',u);el.addEventListener('pointercancel',u)});

function stick(zone,ai){const k=zone.querySelector('i');let pid=null,o,R;
 const mv=e=>{if(e.pointerId!==pid)return;let x=(e.clientX-o.x)/R,y=(e.clientY-o.y)/R,m=Math.hypot(x,y);
  if(m>1){x/=m;y/=m;m=1}
  k.style.transform=`translate(${x*R*.7}px,${y*R*.7}px)`;
  if(m<S.dz){st.a[ai]=0;st.a[ai+1]=0}else{const f=(m-S.dz)/(1-S.dz)/m;st.a[ai]=x*f;st.a[ai+1]=y*f}};
 zone.addEventListener('pointerdown',e=>{if(pid!==null)return;e.preventDefault();pid=e.pointerId;zone.setPointerCapture(pid);
  const r=zone.getBoundingClientRect();R=r.width*.28;o={x:r.left+r.width/2,y:r.top+r.height/2};zone.classList.add('act');vib(10);mv(e)});
 zone.addEventListener('pointermove',mv);
 const up=e=>{if(e.pointerId!==pid)return;pid=null;zone.classList.remove('act');k.style.transform='';st.a[ai]=0;st.a[ai+1]=0};
 zone.addEventListener('pointerup',up);zone.addEventListener('pointercancel',up)}
stick($('#lz'),0);stick($('#rz'),2);

const f=v=>(v>=0?'+':'')+v.toFixed(2);let last='';
(function loop(){if(root.classList.contains('show')){const p=Object.keys(st.b).filter(k=>st.b[k]).join(' + ');
 const t=(p||'Ready')+'\nLS '+f(st.a[0])+' '+f(st.a[1])+'\nRS '+f(st.a[2])+' '+f(st.a[3]);
 if(t!==last){$('#ev').textContent=t;last=t}}
 requestAnimationFrame(loop)})();

const setEl=$('#set');
$('#gear').onclick=()=>setEl.classList.add('show');
$('#done').onclick=()=>setEl.classList.remove('show');
setEl.addEventListener('pointerdown',e=>{if(e.target===setEl)setEl.classList.remove('show')});
$('#fs').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else{await document.documentElement.requestFullscreen();await screen.orientation.lock('landscape')}}catch(e){$('#ev').textContent='Fullscreen not available here'}};
if(!canVib)$('#vnote').textContent='Not supported on this browser (iOS Safari has no vibration)';
function theme(){const d=root;if(S.theme=='auto')d.removeAttribute('data-theme');else d.setAttribute('data-theme',S.theme);d.style.setProperty('--s',S.size);d.style.setProperty('--ss',S.ssize)}
const bind=(id,type)=>{const el=$('#'+id);if(type=='bool')el.checked=!!S[id];else el.value=S[id];
 el.addEventListener('input',()=>{S[id]=type=='bool'?el.checked:el.value;save();theme();if(id=='vib'&&el.checked)vib(20)})};
bind('vib','bool');['str','dz','size','ssize','theme'].forEach(i=>bind(i));theme();
root.addEventListener('contextmenu',e=>e.preventDefault());

/* ---------- wire layer: the whole pad state goes out as one 7-byte report ---------- */
const T_PAD=0x40,T_REL=0x22;
let ready=false,inflight=0,lastKey='',lastSend=0,tapMask=0;
function frame(){   // [buttons: 3 bytes][LX][LY][RX][RY] axes are signed -127..127, down/right positive
 let m=tapMask;BITS.forEach((n,i)=>{if(st.b[n])m|=1<<i});
 const q=v=>Math.max(-127,Math.min(127,Math.round(v*127)))&255;
 return[m&255,(m>>8)&255,(m>>16)&255,q(st.a[0]),q(st.a[1]),q(st.a[2]),q(st.a[3])];
}
function flush(){   // send only on change; resend every 250 ms while held so a lost report heals itself
 if(!ready||inflight>=2)return;
 const f=frame(),key=f.join(),now=performance.now();
 if(key===lastKey&&(now-lastSend<250||f.every(v=>!v)))return;
 tapMask=0;lastKey=key;lastSend=now;inflight++;
 if(!Bridge.send(T_PAD,f,{noResponse:true,onDone:ok=>{inflight=Math.max(0,inflight-1);if(!ok)lastKey=''}}))inflight--;
}
setInterval(flush,20);
function releaseAll(send){
 if(send&&ready)Bridge.send(T_REL,[]);
 Object.keys(st.b).forEach(k=>st.b[k]=0);st.a=[0,0,0,0];tapMask=0;lastKey='';
 root.querySelectorAll('.down').forEach(e=>e.classList.remove('down'));
 root.querySelectorAll('.zone').forEach(z=>{z.classList.remove('act');z.querySelector('i').style.transform=''});
}
function setReady(r){if(r===ready)return;if(!r)releaseAll(false);ready=r;inflight=0;lastKey='';root.classList.toggle('off',!r)}
window.PadUI={releaseAll,onHide(){releaseAll(true)}};
$('#gpBack').onclick=$('#gpRotBack').onclick=()=>Bridge.closeGamepad();
Bridge.onReady(setReady);
})();
