/* Workspace navigation and local signature setup. */
(() => {
  const $ = id => document.getElementById(id);
  const buttons = [...document.querySelectorAll('[data-step]')];
  let entities = [];
  let active = 'client-panel';
  function available() { return buttons.filter(b => b.dataset.step !== 'services-panel' || $('generate-services').checked); }
  window.studioNavigate = id => {
    if (!buttons.some(b => b.dataset.step === id)) return;
    if (id === 'services-panel' && !$('generate-services').checked) id = 'documents-panel';
    active = id;
    buttons.forEach(b => {
      const selected = b.dataset.step === id;
      $(b.dataset.step).classList.toggle('inactive-stage', !selected);
      if (selected) b.setAttribute('aria-current','step'); else b.removeAttribute('aria-current');
      b.disabled = b.dataset.step === 'services-panel' && !$('generate-services').checked;
    });
    const step = buttons.findIndex(b => b.dataset.step === id);
    $('stage-label').textContent = `${String(step+1).padStart(2,'0')} / 05 · ${buttons[step].textContent.trim().replace(/^\d+\s*/, '')}`;
    $('stage-progress').className = `progress-${step+1}`;
    $('step-back').disabled = available()[0].dataset.step === id;
    const last = available().at(-1).dataset.step === id;
    $('step-next').textContent = last ? 'Review draft →' : 'Continue →';
    $('step-caption').textContent = `${step+1} of 5 steps`;
    window.scrollTo({top:0,behavior:'smooth'});
  };
  buttons.forEach(b => b.addEventListener('click',() => window.studioNavigate(b.dataset.step)));
  $('step-back').addEventListener('click',() => {
    const list=available(), index=list.findIndex(b=>b.dataset.step===active);
    if(index>0) window.studioNavigate(list[index-1].dataset.step);
  });
  $('step-next').addEventListener('click',() => {
    const list=available(), index=list.findIndex(b=>b.dataset.step===active);
    if(index<list.length-1) window.studioNavigate(list[index+1].dataset.step);
    else $('preview-button').click();
  });
  $('generate-services').addEventListener('change',() => window.studioNavigate(active));
  $('new-client').addEventListener('click',() => window.studioNavigate('client-panel'));
  $('saved-client').addEventListener('change',() => window.studioNavigate('client-panel'));
  $('signature-upload').addEventListener('change',async event => {
    const file=event.target.files[0]; if(!file) return;
    const status=$('signature-status');
    if(file.size>2000000) { status.textContent='Choose a PNG or JPEG smaller than 2 MB.'; return; }
    const upload=event.target; upload.disabled=true;
    status.textContent='Saving signature on this computer…';
    try {
      const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
      const response=await fetch('/api/signature',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify({image:encoded,entity:$("entity").value})});
      const result=await response.json(); if(!response.ok) throw new Error(result.error);
      status.textContent='Signature saved. It will be included in new agreements.';
      $('include-signature').checked=true;
      const entry=entities.find(e=>e.name===$('entity').value); if(entry) entry.signature_available=true;
    } catch(error) { status.textContent=error.message||'The signature could not be saved.'; }
    finally { upload.disabled=false; upload.value=''; }
  });
  window.updateCompanyEntity = (resetAddress=true) => {
    const entry=entities.find(e=>e.name===$('entity').value); if(!entry) return;
    $('entity-signatory').textContent=`Authorized signatory: ${entry.signatory}, ${entry.title}`;
    $('entity-tag').textContent=entry.short;
    $('signatory-name').textContent=entry.signatory;
    $('signatory-title').textContent=`${entry.title} · ${entry.name}`;
    document.querySelector('.summary-entity').textContent=entry.name;
    $('signature-status').textContent=entry.signature_available ? `${entry.signatory}'s signature is saved and ready to use.` : `Upload ${entry.signatory}'s authorized signature image.`;
    if(resetAddress){ $('provider_address').value=entry.address; $('provider_email').value=entry.email; $('governing_state').value=entry.governing_state||''; $('governing_state').dispatchEvent(new Event('input',{bubbles:true})); }
  };
  $('entity').addEventListener('change',()=>window.updateCompanyEntity(true));
  fetch('/api/config').then(r=>r.json()).then(c=>{entities=c.entities||[];if($('catalog-total')) $('catalog-total').textContent=String((c.catalog||[]).length);window.updateCompanyEntity(false);}).catch(()=>{});
  window.studioNavigate(active);
})();
