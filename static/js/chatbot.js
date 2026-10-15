// Asistente de EcoSoporte Digital: venta de componentes, solicitud de servicios y preguntas frecuentes.
(function(){
  var token=null,busy=false;
  function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
  var btn=el('button','cb-fab');btn.setAttribute('aria-label','Abrir asistente');btn.innerHTML='<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.9 7L3 21l2-6A8 8 0 1 1 21 12z"/></svg>';
  var box=el('section','cb-box');box.setAttribute('aria-label','Asistente EcoSoporte');box.hidden=true;
  var head=el('header','cb-head');head.appendChild(el('b',null,'Asistente EcoSoporte'));
  var close=el('button','cb-close','×');close.setAttribute('aria-label','Cerrar');head.appendChild(close);
  var log=el('div','cb-log');log.setAttribute('aria-live','polite');
  var opts=el('div','cb-opts');
  var form=el('form','cb-form');var inp=el('input');inp.type='text';inp.maxLength=400;inp.placeholder='Escribe tu mensaje…';inp.setAttribute('aria-label','Mensaje');
  var send=el('button',null,'Enviar');send.type='submit';form.appendChild(inp);form.appendChild(send);
  box.appendChild(head);box.appendChild(log);box.appendChild(opts);box.appendChild(form);
  document.body.appendChild(btn);document.body.appendChild(box);
  function bubble(text,who){var b=el('div','cb-msg '+who,text);log.appendChild(b);log.scrollTop=log.scrollHeight}
  function show(r){(r.replies||[]).forEach(function(t){bubble(t,'bot')});opts.innerHTML='';(r.options||[]).forEach(function(o){var b=el('button','cb-opt',o);b.type='button';b.addEventListener('click',function(){say(o)});opts.appendChild(b)});log.scrollTop=log.scrollHeight}
  function post(url,body){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(function(r){return r.json()})}
  function start(){if(token)return;post('/api/chat/start',{}).then(function(r){token=r.token;show(r)}).catch(function(){bubble('No pude conectarme. Intenta de nuevo más tarde.','bot')})}
  function say(text){if(busy||!token||!text.trim())return;busy=true;bubble(text,'user');opts.innerHTML='';inp.value='';
    post('/api/chat/message',{token:token,text:text}).then(function(r){if(r.error){bubble(r.error,'bot')}else show(r)}).catch(function(){bubble('Se perdió la conexión. Intenta de nuevo.','bot')}).then(function(){busy=false})}
  btn.addEventListener('click',function(){box.hidden=!box.hidden;if(!box.hidden){start();inp.focus()}});
  close.addEventListener('click',function(){box.hidden=true});
  form.addEventListener('submit',function(e){e.preventDefault();say(inp.value)});
})();
