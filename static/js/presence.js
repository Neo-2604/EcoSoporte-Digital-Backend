// Latido de presencia: avisa al servidor que esta pestaña sigue abierta (visitas y usuarios conectados).
(function(){
  var sid;
  try{sid=localStorage.getItem('eco-sid');if(!sid){sid=(crypto.randomUUID?crypto.randomUUID():Math.random().toString(36).slice(2)+Date.now().toString(36)).replace(/[^A-Za-z0-9_-]/g,'');localStorage.setItem('eco-sid',sid)}}catch(e){sid=Math.random().toString(36).slice(2)+Date.now().toString(36)}
  function ping(view){
    if(document.visibilityState==='hidden'&&!view)return;
    fetch('/api/v1/ping',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sid:sid,kind:'web',path:location.pathname,view:!!view}),keepalive:true}).catch(function(){});
  }
  ping(true);
  setInterval(function(){ping(false)},60000);
  document.addEventListener('visibilitychange',function(){if(document.visibilityState==='visible')ping(false)});
})();
