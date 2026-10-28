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
  window.studioActive = () => active;
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
  window.updateCompanyEntity = (resetAddress=true) => {
    const entry=entities.find(e=>e.name===$('entity').value); if(!entry) return;
    $('entity-signatory').textContent=`Authorized signatory: ${entry.signatory}, ${entry.title}`;
    $('entity-tag').textContent=entry.short;
    $('signatory-name').textContent=entry.signatory;
    $('signatory-title').textContent=`${entry.title} · ${entry.name}`;
    document.querySelector('.summary-entity').textContent=entry.name;
    // The company signature comes from Admin > Company settings; show it exactly as it sits on the signature line.
    const preview=$('signature-preview');
    preview.hidden=!entry.signature_available;
    if(entry.signature_available) preview.src=`/api/companies/${encodeURIComponent(entry.key)}/signature.png?v=${Date.now()}`;
    $('signature-status').textContent=entry.signature_available ? `${entry.signatory}'s signature is saved and signs the company side automatically.` : `No signature is saved for ${entry.signatory} yet. A Super Admin adds it in Admin › Company settings; until then the company signing line is left blank.`;
    $('signature-status').classList.toggle('field-warning',!entry.signature_available);
    if(resetAddress){ $('provider_address').value=entry.address; $('provider_email').value=entry.email; $('governing_state').value=entry.governing_state||''; $('governing_state').dispatchEvent(new Event('input',{bubbles:true})); }
    window.setCompanyCatalog?.();   // show this company's own services (custom companies) or the standard catalogue
  };
  $('entity').addEventListener('change',()=>window.updateCompanyEntity(true));
  fetch('/api/config').then(r=>r.json()).then(c=>{entities=c.entities||[];if($('catalog-total')) $('catalog-total').textContent=String((c.catalog||[]).length);window.updateCompanyEntity(false);}).catch(()=>{});
  window.studioNavigate(active);
})();
