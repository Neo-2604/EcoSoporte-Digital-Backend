// Cierra el panel de edición de usuario al pulsar fuera o con Esc
(function(){
  function closeAll(){document.querySelectorAll('details.edit-details[open]').forEach(d=>d.removeAttribute('open'))}
  document.addEventListener('click',e=>{
    const d=document.querySelector('details.edit-details[open]');
    if(d&&!e.target.closest('details.edit-details>.edit-user-form')&&!e.target.closest('details.edit-details>summary'))closeAll();
  });
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAll()});
})();

// Menú de perfil (barra superior)
(function(){
  const btn=document.getElementById('profileBtn'),menu=document.getElementById('profileMenu');
  if(!btn||!menu)return;
  const set=o=>{menu.hidden=!o;btn.setAttribute('aria-expanded',o?'true':'false')};
  btn.addEventListener('click',e=>{e.stopPropagation();set(menu.hidden)});
  document.addEventListener('click',e=>{if(!menu.hidden&&!menu.contains(e.target))set(false)});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!menu.hidden){set(false);btn.focus()}});
})();

// Menú superior: desplegables y panel lateral en celular
(function(){
  const nav=document.getElementById('topnav');if(!nav)return;
  const toggle=document.getElementById('menuToggle'),close=document.getElementById('menuClose'),scrim=document.getElementById('navScrim');
  const groups=[].slice.call(nav.querySelectorAll('.nav-group'));
  const closeGroups=except=>groups.forEach(g=>{if(g!==except){g.classList.remove('open');g.querySelector('.nav-trigger').setAttribute('aria-expanded','false')}});
  groups.forEach(g=>{
    const b=g.querySelector('.nav-trigger');
    b.addEventListener('click',e=>{e.stopPropagation();const o=!g.classList.contains('open');closeGroups(g);g.classList.toggle('open',o);b.setAttribute('aria-expanded',o?'true':'false')});
  });
  document.addEventListener('click',e=>{if(!e.target.closest('.nav-group'))closeGroups()});
  const setDrawer=o=>{nav.classList.toggle('open',o);scrim.hidden=!o;toggle&&toggle.setAttribute('aria-expanded',o?'true':'false');document.body.style.overflow=o?'hidden':''};
  toggle&&toggle.addEventListener('click',()=>setDrawer(true));
  close&&close.addEventListener('click',()=>setDrawer(false));
  scrim&&scrim.addEventListener('click',()=>setDrawer(false));
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeGroups();setDrawer(false)}});
})();

// Notificaciones: campana, avisos emergentes y avisos del navegador (conexiones, tickets nuevos y asignaciones)
(function(){
  const btn=document.getElementById('notifBtn');if(!btn)return;
  const panel=document.getElementById('notifPanel'),list=document.getElementById('notifList'),badge=document.getElementById('notifBadge'),bro=document.getElementById('notifBrowser');
  let items=[],seen=0,maxId=0,first=true,stack;
  const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const ago=t=>{const d=(Date.now()-new Date(String(t).replace(' ','T')+(/Z|[+-]\d\d:?\d\d$/.test(String(t))?'':'Z')).getTime())/1000;if(isNaN(d))return '';if(d<60)return 'ahora';if(d<3600)return Math.floor(d/60)+' min';if(d<86400)return Math.floor(d/3600)+' h';return Math.floor(d/86400)+' d'};
  const href=n=>n.ticket_id?'/tickets/'+n.ticket_id:(n.kind==='conexion'?'/actividad':'#');
  const icon=n=>({ticket:'🎫',asignacion:'📌',estado:'🔄',nota:'💬'})[n.kind]||'🟢';
  function render(){
    const unread=items.filter(n=>n.id>seen).length;
    badge.hidden=!unread;badge.textContent=unread>99?'99+':unread;
    list.innerHTML=items.length?items.map(n=>'<li class="'+(n.id>seen?'new':'')+'"><a href="'+href(n)+'"><span class="np-dot '+esc(n.kind)+'">'+icon(n)+'</span><span class="np-tx"><b>'+esc(n.title)+'</b><small>'+esc(n.body||'')+'</small><small>'+ago(n.created_at)+'</small></span></a></li>').join(''):'<li class="np-empty">Sin notificaciones todavía.</li>';
  }
  function toast(n){
    if(!stack){stack=document.createElement('div');stack.className='toast-stack';document.body.appendChild(stack)}
    const a=document.createElement('a');a.className='toast';a.href=href(n);a.innerHTML='<b>'+esc(n.title)+'</b><small>'+esc(n.body||'')+'</small>';
    stack.appendChild(a);setTimeout(()=>a.remove(),9000);
    if('Notification' in window&&Notification.permission==='granted'&&document.visibilityState==='hidden'){try{new Notification(n.title,{body:n.body||''})}catch(e){}}
  }
  async function poll(){
    try{
      const r=await fetch('/api/v1/notifications?after=0',{credentials:'same-origin'});if(!r.ok)return;
      const d=await r.json();seen=d.seen||0;
      const fresh=(d.items||[]).filter(n=>n.id>maxId);
      if(!first)fresh.slice().reverse().forEach(toast);
      first=false;items=d.items||[];maxId=Math.max(maxId,...items.map(n=>n.id),0);render();
    }catch(e){}
  }
  async function markRead(){
    if(!items.length)return;const up=Math.max(...items.map(n=>n.id));
    try{await fetch('/api/v1/notifications/read',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({upto:up})});seen=up;setTimeout(render,1200)}catch(e){}
  }
  btn.addEventListener('click',e=>{e.stopPropagation();const o=panel.hidden;panel.hidden=!o;btn.setAttribute('aria-expanded',o?'true':'false');if(o)markRead()});
  document.addEventListener('click',e=>{if(!panel.hidden&&!panel.contains(e.target)&&e.target!==btn){panel.hidden=true;btn.setAttribute('aria-expanded','false')}});
  if(bro){
    if(!('Notification' in window)||Notification.permission==='granted'||Notification.permission==='denied')bro.hidden=true;
    bro.addEventListener('click',()=>{Notification.requestPermission().then(p=>{if(p!=='default')bro.hidden=true})});
  }
  poll();setInterval(()=>{if(document.visibilityState==='visible')poll()},20000);
})();
