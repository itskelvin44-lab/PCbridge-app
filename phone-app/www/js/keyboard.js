// Keyboard + mouse overlay. Talks to the PC only through window.Bridge (see app.js).
(function(){
const root=document.getElementById('kbView');
const $=s=>root.querySelector(s);
const S={vib:true,str:16,snd:false,hold:1,sens:1.4,theme:'auto'};
try{Object.assign(S,JSON.parse(localStorage.getItem('vkm')||'{}'))}catch(e){}
const save=()=>{try{localStorage.setItem('vkm',JSON.stringify(S))}catch(e){}};

/* ---------- feedback ---------- */
const canVib=!!navigator.vibrate;
const vib=(m)=>{if(S.vib&&canVib)try{navigator.vibrate(m||+S.str)}catch(e){}};
let ac;const tick=()=>{if(!S.snd)return;try{ac=ac||new(window.AudioContext||window.webkitAudioContext)();const o=ac.createOscillator(),g=ac.createGain();o.frequency.value=1300;g.gain.setValueAtTime(.05,ac.currentTime);g.gain.exponentialRampToValueAtTime(.001,ac.currentTime+.04);o.connect(g);g.connect(ac.destination);o.start();o.stop(ac.currentTime+.05)}catch(e){}};
const ev=t=>{$('#ev').textContent=t};

/* ---------- keyboard ---------- */
const SH={'`':'~','1':'!','2':'@','3':'#','4':'$','5':'%','6':'^','7':'&','8':'*','9':'(','0':')','-':'_','=':'+','[':'{',']':'}','\\':'|',';':':',"'":'"',',':'<','.':'>','/':'?'};
const rowsDef=[
 [['Esc',1.25,'Esc'],...Array.from({length:12},(_,i)=>['F'+(i+1),1,'F'+(i+1)]),['PrtSc',.9,'PrtSc'],['Del',.85,'Delete']],
 [...'`1234567890-='.split(''),['⌫',2,'Backspace']],
 [['Tab',1.5,'Tab'],...'qwertyuiop[]'.split(''),['\\',1.5,'\\']],
 [['Caps',1.75,'Caps'],..."asdfghjkl;'".split(''),['Enter',2.25,'Enter']],
 [['Shift',2.25,'Shift'],...'zxcvbnm,./'.split(''),['Shift',2.75,'Shift']],
 [['Ctrl',1.3,'Ctrl'],['Fn',1,'Fn'],['Win',1,'Win'],['Alt',1.3,'Alt'],['',5.2,'Space'],['Alt',1.1,'Alt'],['Ctrl',1.1,'Ctrl'],['←',1,'Left'],['',1,[['↑','Up'],['↓','Down']]],['→',1,'Right']]
];
const MOD={Shift:1,Ctrl:1,Alt:1,Win:1},REP={Backspace:1,Left:1,Right:1,Up:1,Down:1};
const st={Shift:0,Ctrl:0,Alt:0,Win:0,Caps:0};
const K=[];let typed='';
const kb=$('#kb');
const mk=(l,w,c)=>{const k={l,w,c};const el=document.createElement('div');el.className='key'+(MOD[c]||c=='Caps'||c=='Fn'||c=='Win'?' mod':'');el.dataset.i=K.length;k.el=el;K.push(k);return el};
rowsDef.forEach((r,ri)=>{const row=document.createElement('div');row.className='row'+(ri==0?' fn':'');
 r.forEach(x=>{x=typeof x=='string'?[x,1,x]:x;let el;
  if(Array.isArray(x[2])){el=document.createElement('div');el.className='stack';x[2].forEach(a=>el.appendChild(mk(a[0],1,a[1])))}
  else el=mk(x[0],x[1],x[2]);
  el.style.flexGrow=x[1];row.appendChild(el)});
 kb.appendChild(row)});
const isLetter=c=>/^[a-z]$/.test(c);
const val=k=>{const up=(st.Shift>0)!==!!st.Caps;if(isLetter(k.c))return up?k.c.toUpperCase():k.c;if(SH[k.c])return st.Shift>0?SH[k.c]:k.c;return k.c.length==1?k.c:(k.c=='Space'?'Space':k.c)};
function refresh(){K.forEach(k=>{const single=k.c.length==1;k.el.textContent=single?val(k):k.l;
 if(MOD[k.c]||k.c=='Caps'){const s=st[k.c];k.el.classList.toggle('on',s==1);k.el.classList.toggle('lock',s==2)}})}
refresh();
/* ---------- wire layer: turns UI actions into bridge messages ---------- */
const T={KEY_DOWN:0x20,KEY_UP:0x21,RELEASE_ALL:0x22,KEY_TAP:0x23,MOUSE_MOVE:0x30,MOUSE_BTN:0x31,MOUSE_WHEEL:0x32};
const BTN={Left:1,Right:2,Middle:3};
const MODHID={Ctrl:0xE0,Shift:0xE1,Alt:0xE2,Win:0xE3};   // USB HID keyboard usage IDs
const HIDN={Esc:0x29,Backspace:0x2A,Tab:0x2B,Space:0x2C,Enter:0x28,Caps:0x39,Delete:0x4C,PrtSc:0x46,Right:0x4F,Left:0x50,Down:0x51,Up:0x52,'-':0x2D,'=':0x2E,'[':0x2F,']':0x30,';':0x33,"'":0x34,'`':0x35,',':0x36,'.':0x37,'/':0x38};
HIDN['\\']=0x31;for(let i=1;i<=12;i++)HIDN['F'+i]=0x39+i;
const usageOf=c=>/^[a-z]$/.test(c)?c.charCodeAt(0)-93:/^[1-9]$/.test(c)?0x1D+ +c:c==='0'?0x27:(HIDN[c]||0);
let ready=false;
const held=new Map(),wire={Ctrl:0,Shift:0,Alt:0,Win:0};
const tx=(type,p,o)=>ready&&window.Bridge?Bridge.send(type,p,o):false;
// Modifiers are real key-down/up on the PC: held while armed, or while a key that consumed them is held.
function syncMods(){
 const want={};for(const m in MODHID)if(st[m]>0)want[m]=1;
 held.forEach(h=>{if(h.mods)h.mods.forEach(m=>want[m]=1)});
 for(const m in MODHID){
  if(want[m]&&!wire[m]){if(tx(T.KEY_DOWN,[MODHID[m]]))wire[m]=1}
  else if(!want[m]&&wire[m]){tx(T.KEY_UP,[MODHID[m]]);wire[m]=0}}
}
function echo(k,v){
 const p=[];if(st.Win)p.push('Win');if(st.Ctrl)p.push('Ctrl');if(st.Alt)p.push('Alt');if(st.Shift&&v.length>1)p.push('Shift');p.push(v);
 ev('key '+p.join('+'));
 if(!st.Ctrl&&!st.Alt&&!st.Win){if(v.length==1)typed+=v;else if(v=='Space')typed+=' ';else if(v=='Enter')typed+='↵';else if(v=='Backspace')typed=typed.slice(0,-1);
  typed=typed.slice(-60);$('#typed').textContent=typed}
}
let tShift=0;
function pressMod(k){
 const now=performance.now();
 if(k.c=='Shift'){st.Shift=(st.Shift==1&&now-tShift<350)?2:(st.Shift?0:1);tShift=now}else st[k.c]=st[k.c]?0:1;
 ev(k.c+(st[k.c]==2?' lock':st[k.c]?' on':' off'));refresh();syncMods();
}
function keyDown(k,h){
 vib();tick();
 if(MOD[k.c])return pressMod(k);
 if(k.c=='Caps'){st.Caps^=1;ev('Caps '+(st.Caps?'on':'off'));refresh();tx(T.KEY_TAP,[0x39]);return}
 if(k.c=='Fn'){ev('Fn (local only, not sent)');return}
 const v=val(k);echo(k,v);
 const u=usageOf(k.c);if(!u){ev('no key code for '+k.c);return}
 h.mods=['Ctrl','Alt','Shift','Win'].filter(m=>st[m]==1);   // one-shot modifiers get used up by this key
 h.mods.forEach(m=>st[m]=0);refresh();h.u=u;
 if(REP[k.c]){h.rep=1;tx(T.KEY_TAP,[u]);   // repeating keys: phone generates the repeats
  h.t=setTimeout(()=>{h.i=setInterval(()=>{vib(Math.max(5,S.str/2));tx(T.KEY_TAP,[u]);echo(k,v)},55)},380)}
 else tx(T.KEY_DOWN,[u]);                  // normal keys: held down until the finger lifts
}
kb.addEventListener('pointerdown',e=>{const el=e.target.closest('.key');if(!el)return;e.preventDefault();
 const k=K[el.dataset.i],h={el,k};el.classList.add('down');held.set(e.pointerId,h);
 try{el.setPointerCapture(e.pointerId)}catch(_){}
 keyDown(k,h)});
const rel=e=>{const h=held.get(e.pointerId);if(!h)return;h.el.classList.remove('down');clearTimeout(h.t);clearInterval(h.i);held.delete(e.pointerId);
 if(h.u&&!h.rep)tx(T.KEY_UP,[h.u]);
 if(h.mods&&h.mods.length)syncMods()};
kb.addEventListener('pointerup',rel);kb.addEventListener('pointercancel',rel);

/* ---------- mouse ---------- */
const pad=$('#pad'),surf=$('#surf'),cur=$('#cur'),mbtn=$('#mouse');
let cx=100,cy=80,scrollY=0;
const place=()=>{cur.style.transform=`translate(${cx}px,${cy}px)`};
function showPad(v){if(!v)releaseMouse();pad.classList.toggle('show',v);mbtn.classList.toggle('on',v);moveTimer(v);
 if(v){const r=surf.getBoundingClientRect();cx=Math.min(cx,r.width-4)||r.width/2;cy=Math.min(cy,r.height-4)||r.height/2;place()}
 ev(v?'Mouse pad open':'Mouse pad closed');vib(10)}
mbtn.addEventListener('pointerdown',e=>{e.preventDefault();if(+S.hold)showPad(true);else showPad(!pad.classList.contains('show'))});
const mEnd=()=>{if(+S.hold&&pad.classList.contains('show'))showPad(false)};
mbtn.addEventListener('pointerup',mEnd);mbtn.addEventListener('pointercancel',mEnd);
mbtn.addEventListener('contextmenu',e=>e.preventDefault());

const rip=()=>{const r=document.createElement('span');r.className='rip';r.style.left=cx+'px';r.style.top=cy+'px';surf.appendChild(r);setTimeout(()=>r.remove(),450)};
const click=n=>{rip();vib(12);tick();ev(n+' click');tx(T.MOUSE_BTN,[BTN[n],2])};
const scroll=d=>{scrollY+=d;surf.style.backgroundPosition='0 '+scrollY+'px';ev('scroll '+(d>0?'up':'down')+' '+Math.abs(Math.round(d)));wheel(d)};
const P=new Map();let g={max:0,moved:0,done:0},lastTap=0,drag=false;
surf.addEventListener('pointerdown',e=>{e.preventDefault();surf.setPointerCapture(e.pointerId);const n=performance.now();
 P.set(e.pointerId,{x:e.clientX,y:e.clientY,t:n});
 if(P.size==1){g={max:1,moved:0,done:0};if(n-lastTap<300){drag=true;ev('LMB down (drag lock)');vib(18);tx(T.MOUSE_BTN,[1,1])}}else g.max=Math.max(g.max,P.size)});
surf.addEventListener('pointermove',e=>{const p=P.get(e.pointerId);if(!p)return;
 const dx=e.clientX-p.x,dy=e.clientY-p.y;p.x=e.clientX;p.y=e.clientY;g.moved+=Math.abs(dx)+Math.abs(dy);
 if(P.size>=2){if(g.moved>10)scroll(dy/2);return}
 const f=+S.sens*(1+Math.min(Math.hypot(dx,dy)/10,1.5));const r=surf.getBoundingClientRect();
 cx=Math.max(0,Math.min(r.width-2,cx+dx*f));cy=Math.max(0,Math.min(r.height-2,cy+dy*f));place();queueMove(dx*f,dy*f);
 ev((drag?'drag ':'move ')+(dx*f>=0?'+':'')+Math.round(dx*f)+', '+(dy*f>=0?'+':'')+Math.round(dy*f))});
const up=e=>{const p=P.get(e.pointerId);if(!p)return;P.delete(e.pointerId);const n=performance.now(),short=g.moved<10&&n-p.t<250;
 if(short&&g.max>=2&&!g.done){g.done=1;click('Right')}
 else if(short&&g.max==1&&!drag){lastTap=n;click('Left')}
 if(P.size==0&&drag){drag=false;ev('LMB up');vib(10);tx(T.MOUSE_BTN,[1,0])}};
surf.addEventListener('pointerup',up);surf.addEventListener('pointercancel',up);

let sy=null,sm=0;const strip=$('#strip');
strip.addEventListener('pointerdown',e=>{e.preventDefault();strip.setPointerCapture(e.pointerId);sy=e.clientY;sm=0;strip.classList.add('down')});
strip.addEventListener('pointermove',e=>{if(sy==null)return;const d=e.clientY-sy;if(Math.abs(d)>6){sm=1;scroll(d);sy=e.clientY;vib(4)}});
const sEnd=e=>{if(sy==null)return;sy=null;strip.classList.remove('down');if(e.type=='pointerup'&&!sm){vib();tick();ev('MMB click');tx(T.MOUSE_BTN,[3,2])}};strip.addEventListener('pointerup',sEnd);strip.addEventListener('pointercancel',sEnd);

[['#lmb','LMB'],['#rmb','RMB']].forEach(([s,n])=>{const b=$(s);
 b.addEventListener('pointerdown',e=>{e.preventDefault();b.setPointerCapture(e.pointerId);b.classList.add('down');vib();tick();ev(n+' down');tx(T.MOUSE_BTN,[n=='LMB'?1:2,1])});
 const u=()=>{if(b.classList.contains('down')){b.classList.remove('down');ev(n+' up');tx(T.MOUSE_BTN,[n=='LMB'?1:2,0])}};
 b.addEventListener('pointerup',u);b.addEventListener('pointercancel',u)});

/* ---------- settings ---------- */
const setEl=$('#set');
$('#gear').onclick=()=>setEl.classList.add('show');
const side=$('#side'),hideB=$('#hide');
const applySide=()=>{side.classList.toggle('min',!!S.min);hideB.textContent=S.min?'›':'‹';hideB.setAttribute('aria-label',S.min?'Show mouse column':'Hide mouse column')};
hideB.onclick=()=>{S.min=!S.min;if(S.min&&pad.classList.contains('show'))showPad(false);save();applySide();vib(10);ev(S.min?'Side column hidden':'Side column shown')};
applySide();
$('#done').onclick=()=>setEl.classList.remove('show');
setEl.addEventListener('pointerdown',e=>{if(e.target===setEl)setEl.classList.remove('show')});
$('#fs').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else{await document.documentElement.requestFullscreen();await screen.orientation.lock('landscape')}}catch(e){ev('Fullscreen not available here')}};
if(!canVib){$('#vnote').textContent='Not supported on this browser (iOS Safari has no vibration)'}
const bind=(id,key,type)=>{const el=$('#'+id);if(type=='bool')el.checked=!!S[key];else el.value=S[key];
 el.addEventListener('input',()=>{S[key]=type=='bool'?el.checked:el.value;save();if(key=='theme'){theme()}if(key=='vib'&&el.checked)vib(20)})};
function theme(){if(S.theme=='auto')root.removeAttribute('data-theme');else root.setAttribute('data-theme',S.theme)}
bind('vib','vib','bool');bind('snd','snd','bool');bind('str','str');bind('hold','hold');bind('sens','sens');bind('theme','theme');theme();
root.addEventListener('contextmenu',e=>e.preventDefault());

/* ---------- mouse output + connection state ---------- */
let mvx=0,mvy=0,mvT=null,inflight=0,wheelAcc=0;
function queueMove(dx,dy){mvx+=dx;mvy+=dy}
function moveTimer(on){clearInterval(mvT);mvT=on?setInterval(flushMove,25):null;if(!on)mvx=mvy=0}
function flushMove(){   // ~40 updates/s, coalesced; if the link is busy, keep accumulating instead of queueing
 if(!ready){mvx=mvy=0;return}
 const c=v=>Math.max(-32768,Math.min(32767,Math.round(v))),x=c(mvx),y=c(mvy);
 if((!x&&!y)||inflight>=2)return;
 mvx-=x;mvy-=y;inflight++;
 if(!tx(T.MOUSE_MOVE,[x&255,(x>>8)&255,y&255,(y>>8)&255],{noResponse:true,onDone:()=>{inflight=Math.max(0,inflight-1)}}))inflight--;
}
function wheel(d){wheelAcc+=d;const t=Math.max(-127,Math.min(127,Math.trunc(wheelAcc/8)));if(t){wheelAcc-=t*8;tx(T.MOUSE_WHEEL,[t&255])}}
function releaseMouse(send){
 const s=send!==false;
 if(drag){drag=false;if(s)tx(T.MOUSE_BTN,[1,0])}
 [['#lmb',1],['#rmb',2]].forEach(([q,id])=>{const b=$(q);if(b.classList.contains('down')){b.classList.remove('down');if(s)tx(T.MOUSE_BTN,[id,0])}});
 P.clear();strip.classList.remove('down');sy=null;
}
function resetLocal(){
 held.forEach(h=>{h.el.classList.remove('down');clearTimeout(h.t);clearInterval(h.i)});held.clear();
 for(const m in MODHID){st[m]=0;wire[m]=0}refresh();
}
function releaseAll(send){if(send)tx(T.RELEASE_ALL,[]);resetLocal();releaseMouse(false)}
function setReady(r){
 if(r===ready)return;
 if(!r){resetLocal();releaseMouse(false)}
 ready=r;inflight=0;mvx=mvy=0;root.classList.toggle('off',!r);
 ev(r?'Ready':'Not connected - tap Status');
}
window.Keyboard={releaseAll,onHide(){releaseAll(true);if(pad.classList.contains('show'))showPad(false)}};
$('#kbBack').onclick=$('#rotBack').onclick=()=>window.Bridge&&Bridge.closeKeyboard();
Bridge.onReady(setReady);
})();
