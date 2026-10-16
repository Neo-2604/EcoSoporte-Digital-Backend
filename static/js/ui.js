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
