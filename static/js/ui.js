// Cierra el panel de edición de usuario al pulsar fuera o con Esc
(function(){
  function closeAll(){document.querySelectorAll('details.edit-details[open]').forEach(d=>d.removeAttribute('open'))}
  document.addEventListener('click',e=>{
    const d=document.querySelector('details.edit-details[open]');
    if(d&&!e.target.closest('details.edit-details>.edit-user-form')&&!e.target.closest('details.edit-details>summary'))closeAll();
  });
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAll()});
})();
