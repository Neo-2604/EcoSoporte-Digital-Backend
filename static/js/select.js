// Desplegables con el estilo de la página (claro y oscuro) en todas las pantallas.
// El <select> original sigue en su sitio (formularios, validación y valores no cambian);
// solo se reemplaza la lista nativa del sistema por una lista propia, con buscador cuando hay muchas opciones.
(function(){
  let pop=null,current=null,active=-1,items=[];
  const SEARCH_FROM=8;
  const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const eligible=el=>el&&el.tagName==='SELECT'&&!el.multiple&&!(el.size>1)&&!el.disabled&&!el.hasAttribute('data-native');

  function label(text){
    const parts=String(text).split(' · ');
    if(parts.length<2)return '<span class="ns-main">'+esc(text)+'</span>';
    return '<span class="ns-main">'+esc(parts[0])+'</span><span class="ns-sub">'+esc(parts.slice(1).join(' · '))+'</span>';
  }
  function close(refocus){
    if(!pop)return;
    pop.remove();pop=null;
    document.removeEventListener('mousedown',onOutside,true);
    window.removeEventListener('resize',onGone);window.removeEventListener('scroll',onScroll,true);
    if(current){current.classList.remove('ns-open');current.setAttribute('aria-expanded','false');if(refocus)current.focus()}
    current=null;items=[];active=-1;
  }
  function onOutside(e){if(pop&&!pop.contains(e.target)&&e.target!==current)close(false)}
  function onGone(){if(pop&&pop.contains(document.activeElement))return;close(false)}
  function onScroll(e){if(pop&&!pop.contains(e.target))close(false)}

  function place(sel){
    const r=sel.getBoundingClientRect(),vw=window.innerWidth,vh=window.innerHeight;
    const w=Math.min(Math.max(r.width,240),Math.min(440,vw-16));
    let left=Math.min(Math.max(8,r.left),vw-w-8);
    pop.style.width=w+'px';pop.style.left=left+'px';
    const room=vh-r.bottom-12,above=r.top-12;
    const h=Math.min(pop.scrollHeight,320);
    if(room<Math.min(h,220)&&above>room){pop.style.top='auto';pop.style.bottom=(vh-r.top+6)+'px';pop.style.maxHeight=Math.min(320,above)+'px'}
    else{pop.style.bottom='auto';pop.style.top=(r.bottom+6)+'px';pop.style.maxHeight=Math.min(320,Math.max(160,room))+'px'}
  }

  function setActive(i,scroll){
    const vis=items.filter(o=>!o.hidden);if(!vis.length)return;
    i=Math.max(0,Math.min(vis.length-1,i));
    items.forEach(o=>o.classList.remove('is-active'));
    vis[i].classList.add('is-active');active=i;
    if(scroll!==false)vis[i].scrollIntoView({block:'nearest'});
  }
  function choose(sel,opt){
    if(opt.disabled)return;
    const changed=sel.value!==opt.value;
    sel.value=opt.value;
    close(true);
    if(changed){sel.dispatchEvent(new Event('input',{bubbles:true}));sel.dispatchEvent(new Event('change',{bubbles:true}))}
  }

  function open(sel){
    if(pop)close(false);
    current=sel;sel.classList.add('ns-open');sel.setAttribute('aria-expanded','true');
    pop=document.createElement('div');pop.className='ns-pop';pop.setAttribute('role','listbox');
    const many=sel.options.length>=SEARCH_FROM;
    let html=many?'<div class="ns-search"><input type="search" placeholder="Buscar…" aria-label="Buscar en la lista" autocomplete="off"></div>':'';
    html+='<ul class="ns-list">';
    const addOpt=(o,i)=>{
      html+='<li class="ns-opt'+(o.selected?' is-selected':'')+(o.disabled?' is-disabled':'')+'" role="option" data-i="'+i+'" aria-selected="'+(o.selected?'true':'false')+'">'+label(o.text)+'<i class="ns-check" aria-hidden="true">✓</i></li>';
    };
    let i=0;
    Array.from(sel.children).forEach(ch=>{
      if(ch.tagName==='OPTGROUP'){html+='<li class="ns-group">'+esc(ch.label)+'</li>';Array.from(ch.children).forEach(o=>addOpt(o,i++))}
      else{addOpt(ch,i++)}
    });
    html+='</ul><div class="ns-none" hidden>Sin resultados</div>';
    pop.innerHTML=html;document.body.appendChild(pop);
    items=Array.from(pop.querySelectorAll('.ns-opt'));
    const options=Array.from(sel.options);
    items.forEach(li=>{
      li.addEventListener('mousedown',e=>e.preventDefault());
      li.addEventListener('click',()=>choose(sel,options[+li.dataset.i]));
      li.addEventListener('mousemove',()=>{const vis=items.filter(o=>!o.hidden);const k=vis.indexOf(li);if(k>=0&&k!==active)setActive(k,false)});
    });
    place(sel);
    const si=pop.querySelector('.ns-search input');
    const start=Math.max(0,items.findIndex(li=>li.classList.contains('is-selected')));
    setActive(start);
    if(si){
      si.addEventListener('input',()=>{
        const q=si.value.trim().toLowerCase();let any=false;
        items.forEach(li=>{const hit=!q||li.textContent.toLowerCase().includes(q);li.hidden=!hit;if(hit)any=true});
        pop.querySelector('.ns-none').hidden=any;setActive(0);
      });
      if(window.matchMedia&&matchMedia('(pointer:fine)').matches)si.focus({preventScroll:true}); // en el celular no se abre el teclado solo
    }
    pop.addEventListener('keydown',e=>{
      if(e.key==='Escape'){e.preventDefault();close(true)}
      else if(e.key==='ArrowDown'){e.preventDefault();setActive(active+1)}
      else if(e.key==='ArrowUp'){e.preventDefault();setActive(active-1)}
      else if(e.key==='Home'){e.preventDefault();setActive(0)}
      else if(e.key==='End'){e.preventDefault();setActive(9999)}
      else if(e.key==='Enter'){e.preventDefault();const vis=items.filter(o=>!o.hidden);if(vis[active])choose(sel,options[+vis[active].dataset.i])}
      else if(e.key==='Tab'){close(false)}
    });
    pop.tabIndex=-1;if(!si||!(window.matchMedia&&matchMedia('(pointer:fine)').matches))pop.focus({preventScroll:true});
    document.addEventListener('mousedown',onOutside,true);
    window.addEventListener('resize',onGone);window.addEventListener('scroll',onScroll,true);
  }

  document.addEventListener('mousedown',e=>{
    const sel=e.target.closest&&e.target.closest('select');
    if(!eligible(sel)||e.button!==0)return;
    e.preventDefault();sel.focus();
    pop&&current===sel?close(false):open(sel);
  });
  document.addEventListener('keydown',e=>{
    const sel=e.target;
    if(!eligible(sel)||pop)return;
    if(e.key==='Enter'||e.key===' '||(e.key==='ArrowDown'&&e.altKey)){e.preventDefault();open(sel)}
  });
  // la lista del sistema no se usa; por accesibilidad el select avisa que abre una lista
  document.querySelectorAll('select').forEach(s=>{if(!s.hasAttribute('data-native')){s.setAttribute('aria-haspopup','listbox');s.setAttribute('aria-expanded','false')}});
})();
