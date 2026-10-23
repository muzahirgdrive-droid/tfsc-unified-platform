"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const form = $("contract-form");
  const state = {config: null, catalog: [], selected: new Map(), custom: [], clientId: "", filter: "all", busy: false, dirty: false, customCounter: 0, disabledControls: new Map()};
  const modelLabels = {percentage: "Percentage of net collections", fte: "FTE per month", pte: "PTE per month", unit: "Per completed unit", fixed: "Fixed monthly fee", consultancy: "Monthly consultancy fee", one_time: "One project invoice", two_installments: "Two project installments"};
  const plainFields = ["client_name", "address", "client_signatory", "client_title", "client_email", "effective_date", "baa_effective_date", "provider_address", "provider_email", "governing_state", "notice_days", "payment_days", "cure_days", "special_terms", "nda_purpose", "nda_term_years", "nda_survival_years", "output_root"];
  const serviceHints = {
    medical_billing: "Identify providers, payers, practice systems and dates of service. Define any excluded accounts or collections.",
    wc_collection: "Identify assigned workers’ compensation accounts, jurisdictions and retained counsel, if applicable.",
    personal_injury_collection: "Identify assigned personal injury medical accounts, jurisdictions, authorized attorney/insurer contacts and any letters of protection. For FTE pricing, specify staffing and coverage hours.",
    medical_coding: "Define the coding review scope, sample size, specialties, deliverables and review frequency.",
    prior_auth: "Specify authorization or benefit verification tasks, providers, payers and turnaround expectations.",
    virtual_assistant: "Specify administrative duties, schedule, time zone, systems and communication channels.",
    medical_scribe: "Specify live or offline work, specialties, schedule, time zone and documentation workflow.",
    ar_denials: "Define assigned accounts, aging range, dates of service, systems and reporting expectations.",
    rcm_consultancy: "Define the assessment areas, work schedule, deliverables and implementation support.",
    digital_marketing: "Specify channels, deliverables, approval workflow and budget. State whether media spend is separate.",
    telemarketing: "Specify campaign, target audience, approved scripts, calling schedule and consent records.",
    credentialing: "Identify providers, payer panels, states, application types and completion milestones.",
    state_licensing: "Identify applicants, jurisdictions, license types, application scope and completion milestones.",
    compliance_training: "Specify training topics, audience, session format, materials and delivery dates.",
    website_development: "Define pages, features, content, revision rounds, acceptance criteria and handover.",
    consultancy: "Define the question, project scope, deliverables and completion date.",
    mips_consultancy: "Specify performance year, reporting entity, measures, systems and agreed deliverables.",
    coding_consultancy: "Specify specialties, sample size, records reviewed, reporting and education deliverables.",
    other: "Required: define the exact service, deliverables, responsibilities and completion criteria."
  };

  function el(tag, className, content) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (content !== undefined && content !== null) item.textContent = String(content);
    return item;
  }
  function today() {
    const date = new Date();
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }
  function money(value) { return new Intl.NumberFormat("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 2}).format(Number(value) || 0); }
  function numeric(value, fallback = 0) { const n = Number(value); return Number.isFinite(n) ? n : fallback; }
  function decimal(value) {
    let text = String(value ?? "0").trim();
    if (/e/i.test(text)) text = numeric(text).toFixed(12);
    if (!/^\d*(?:\.\d*)?$/.test(text) || !text.replace(".", "")) return {n: 0n, d: 1n};
    const parts = text.split(".");
    return {n: BigInt((parts[0] || "0") + (parts[1] || "")), d: 10n ** BigInt((parts[1] || "").length)};
  }
  function roundHalfUp(numerator, denominator) { return (numerator * 2n + denominator) / (denominator * 2n); }
  function priceCents(rate, quantity = 1) {
    const a = decimal(rate), b = decimal(quantity);
    return roundHalfUp(a.n * b.n * 100n, a.d * b.d);
  }
  function moneyCents(cents) { return `$${(cents / 100n).toLocaleString("en-US")}.${String(cents % 100n).padStart(2, "0")}`; }
  function dateLabel(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return "Select a date";
    const date = new Date(`${value}T12:00:00`);
    return Number.isNaN(date.getTime()) ? "Select a date" : date.toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric"});
  }
  function announce(message, isError = false) {
    const box = $(isError ? "app-error" : "status-message");
    $(isError ? "status-message" : "app-error").hidden = true;
    box.textContent = message;
    box.hidden = false;
    if (isError) box.scrollIntoView({behavior: "smooth", block: "center"});
  }
  function clearMessages() { $("app-error").hidden = true; $("status-message").hidden = true; }
  function markDirty() { state.dirty = true; }

  async function api(path, options = {}) {
    const headers = {Accept: "application/json", ...options.headers};
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = document.querySelector('meta[name="csrf-token"]').content;
    }
    let response;
    try { response = await fetch(path, {...options, headers, cache: "no-store"}); }
    catch (_) { throw new Error("The local workspace is not responding. Open Start TFSC Compliance.cmd to start it, then try again."); }
    let result;
    try { result = await response.json(); }
    catch (_) { throw new Error("The local service returned an unexpected response. Restart Contract Studio and try again."); }
    if (response.status === 401) { location.href = "/login"; throw Error("Please sign in again."); }
    if (!response.ok || result.error) throw new Error(result.error || `The request failed (${response.status}).`);
    return result;
  }

  function serviceDefaults(spec) {
    const model = spec.models[0].id;
    return {id: spec.id, model, rate: "", government_rate: 150, commercial_rate: 150, website_kind: "wordpress", website_prices: {}, quantity: 1, hours: model === "pte" ? 20 : 40, unit: "", details: "", milestone1: "Execution and commencement", milestone2: "Delivery of the agreed final work product", installment_percent: 50};
  }
  function customModels(category) {
    return (category === "recurring" ? ["fte", "pte", "unit", "fixed", "consultancy"] : ["one_time", "two_installments"]).map(id => ({id, label: modelLabels[id]}));
  }
  function normalizeSelection(item, spec) {
    return {...serviceDefaults(spec), ...item, id: spec.id, model: spec.models.some(m => m.id === item.model) ? item.model : spec.models[0].id};
  }

  function renderCatalog() {
    const container = $("service-catalog");
    container.replaceChildren();
    const search = $("service-search").value.toLowerCase().trim();
    let count = 0;
    state.catalog.forEach(spec => {
      if (state.filter !== "all" && spec.category !== state.filter) return;
      if (search && !(spec.name + " " + spec.description).toLowerCase().includes(search)) return;
      count++;
      const label = el("label", "service-option");
      const checkbox = el("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.selected.has(spec.id);
      checkbox.disabled = !$("generate-services").checked;
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.selected.set(spec.id, serviceDefaults(spec));
        else state.selected.delete(spec.id);
        markDirty();
        renderSelected();
        updateSummary();
      });
      const text = el("span", "service-option-name", spec.name);
      text.append(el("small", "", spec.category === "recurring" ? "Recurring engagement" : "On-demand project"));
      label.append(checkbox, text);
      container.append(label);
    });
    $("no-service-results").hidden = count !== 0;
  }

  function makeField(owner, key, labelText, settings = {}) {
    const field = el("div", "field");
    const id = `${owner._key || owner.id}-${key}`;
    const label = el("label", "", labelText);
    label.htmlFor = id;
    if (settings.required) label.append(el("span", "required", " *"));
    let input;
    if (settings.options) {
      input = el("select");
      settings.options.forEach(option => {
        const node = el("option", "", option.label);
        node.value = option.id;
        input.append(node);
      });
    } else if (settings.multiline) {
      input = el("textarea");
      input.rows = settings.rows || 2;
      input.maxLength = settings.maxLength || 2500;
    } else {
      input = el("input");
      input.type = settings.type || "text";
      if (input.type === "number") {
        input.min = settings.min === undefined ? "0.01" : settings.min;
        input.max = settings.max === undefined ? "1000000000" : settings.max;
        input.step = settings.step || "0.01";
      } else input.maxLength = settings.maxLength || 200;
    }
    input.id = id;
    input.value = owner[key] === undefined ? "" : owner[key];
    input.required = !!settings.required;
    if (settings.placeholder) input.placeholder = settings.placeholder;
    input.disabled = !$("generate-services").checked;
    const eventName = settings.options ? "change" : "input";
    input.addEventListener(eventName, () => {
      owner[key] = input.value;
      markDirty();
      if (settings.onChange) settings.onChange(input.value);
      updateSummary();
      document.querySelectorAll(`[data-price-for="${owner._key || owner.id}"]`).forEach(node => { node.textContent = detailedPrice(owner); });
    });
    field.append(label, input);
    if (settings.note) {
      const note = el("p", "field-note", settings.note);
      note.id = `${id}-note`;
      input.setAttribute("aria-describedby", note.id);
      field.append(note);
    }
    return field;
  }

  function detailedPrice(item) {
    if (item.model === "rate_card") return `Government: ${money(item.government_rate)} · Commercial: ${money(item.commercial_rate)} per provider / application, upon request.`;
    const rate = numeric(item.rate), quantity = numeric(item.quantity, 1);
    if (!rate) return "Enter a rate to complete this service’s pricing.";
    if (item.model === "percentage") return `${rate}% of net collections, as defined in the agreement.`;
    if (["fte", "pte"].includes(item.model)) return `${money(rate)} × ${quantity} ${item.model.toUpperCase()} = ${moneyCents(priceCents(item.rate, item.quantity))} / month · ${numeric(item.hours)} scheduled hours per week per resource`;
    if (item.model === "unit") return `${money(rate)} per ${item.unit || "completed unit"} · Planning volume: ${quantity} · Actual completed units are invoiced.`;
    if (["fixed", "consultancy"].includes(item.model)) return `${money(rate)} per month`;
    const totalCents = priceCents(item.rate, item.quantity);
    if (item.model === "two_installments") {
      const percent = numeric(item.installment_percent, 50);
      const fraction = decimal(item.installment_percent || 50);
      const firstCents = roundHalfUp(totalCents * fraction.n, fraction.d * 100n);
      const secondPercent = Number((100 - percent).toFixed(2));
      return `${moneyCents(totalCents)} total · First installment ${percent}%: ${moneyCents(firstCents)} · Second installment ${secondPercent}%: ${moneyCents(totalCents - firstCents)}`;
    }
    return `${money(rate)} × ${quantity} = ${moneyCents(totalCents)} total project fee · One invoice on delivery / completion`;
  }

  function priceFields(item, models) {
    const group = el("div");
    const modelField = makeField(item, "model", "Pricing model", {options: models, required: true, onChange: value => {
      if (value === "fte") item.hours = 40;
      if (value === "pte") item.hours = 20;
      renderSelected();
    }});
    if (item.id === "website_development") {
      group.append(makeField(item, "website_kind", "Website type", {required: true, options: [{id:"wordpress",label:"WordPress website"},{id:"custom",label:"Custom website"}], onChange: value => {
        const old = value === "wordpress" ? "custom" : "wordpress";
        item.website_prices ||= {};
        item.website_prices[old] = item.rate;
        item.rate = item.website_prices[value] || "";
        renderSelected();
      }}));
    }
    group.append(modelField);
    const price = el("p", "service-pricing-line", detailedPrice(item));
    price.dataset.priceFor = item._key || item.id;
    group.append(price);
    const grid = el("div", "field-grid");
    if (item.model === "rate_card") {
      grid.append(makeField(item, "government_rate", "Government payer / provider / application (USD)", {type:"number",required:true}), makeField(item, "commercial_rate", "Commercial payer / provider / application (USD)", {type:"number",required:true}));
      group.append(grid);
      return group;
    }
    let rateLabel = "Monthly fee (USD)";
    if (item.model === "percentage") rateLabel = "Collection fee (%)";
    if (item.model === "fte" || item.model === "pte") rateLabel = `Monthly rate per ${item.model.toUpperCase()} (USD)`;
    if (item.model === "unit") rateLabel = "Price per completed unit (USD)";
    if (["one_time", "two_installments"].includes(item.model)) rateLabel = item.id === "website_development" ? `${item.website_kind === "custom" ? "Custom" : "WordPress"} website price (USD)` : "Price per project / application (USD)";
    grid.append(makeField(item, "rate", rateLabel, {type: "number", required: true, max: item.model === "percentage" ? 100 : 1000000000, placeholder: item.model === "percentage" ? "e.g. 5" : "e.g. 1500"}));
    if (["fte", "pte"].includes(item.model)) {
      grid.append(makeField(item, "quantity", "Number of resources", {type: "number", required: true, max: 1000000, step: "0.01"}));
      grid.append(makeField(item, "hours", "Scheduled hours per week / resource", {type: "number", required: true, max: 168, note: "The agreement records the hours entered here; FTE and PTE are defined by this schedule."}));
    } else if (item.model === "unit") {
      grid.append(makeField(item, "unit", "Unit definition", {required: true, maxLength: 100, placeholder: "e.g. completed prior authorization"}));
      grid.append(makeField(item, "quantity", "Planning volume (units)", {type: "number", required: true, max: 1000000, note: "An estimate for planning, without a minimum purchase commitment."}));
    } else if (["one_time", "two_installments"].includes(item.model)) {
      grid.append(makeField(item, "quantity", "Number of projects / applications", {type: "number", required: true, max: 1000000, step: "0.01"}));
    }
    group.append(grid);
    if (item.model === "two_installments") {
      group.append(makeField(item, "installment_percent", "First installment (%)", {type: "number", required: true, max: 99.99, note: "The second installment is the remaining percentage of the total project fee."}));
      const milestones = el("div", "field-grid");
      milestones.append(makeField(item, "milestone1", "First invoice milestone", {required: true, maxLength: 300, multiline: true}), makeField(item, "milestone2", "Second invoice milestone", {required: true, maxLength: 300, multiline: true}));
      group.append(milestones);
    }
    return group;
  }

  function renderSelected() {
    const selected = $("selected-services");
    selected.replaceChildren();
    if (!state.selected.size && !state.custom.length) {
      const empty = el("div", "selection-empty");
      empty.append(el("span", "", "＋"), el("strong", "", "Your agreement starts here."), el("p", "", "Select a service above to define its pricing and scope."));
      selected.append(empty);
    }
    state.catalog.forEach(spec => {
      const item = state.selected.get(spec.id);
      if (!item) return;
      const card = el("div", "selected-service");
      const header = el("div", "selected-service-header");
      const title = el("div");
      title.append(el("h4", "", spec.name), el("p", "", spec.description));
      const remove = el("button", "remove-service", "Remove");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${spec.name}`);
      remove.addEventListener("click", () => { state.selected.delete(spec.id); markDirty(); renderCatalog(); renderSelected(); updateSummary(); });
      header.append(title, remove);
      const body = el("div", "selected-service-body");
      body.append(priceFields(item, spec.models), makeField(item, "details", "Service parameters & agreed deliverables", {multiline: true, required: spec.id === "other", placeholder: serviceHints[spec.id] || "Describe the service parameters for this engagement.", note: "These details are added to this service’s standard scope and responsibilities."}));
      card.append(header, body);
      selected.append(card);
    });
    const customContainer = $("custom-services");
    customContainer.replaceChildren();
    state.custom.forEach(item => {
      const card = el("div", "selected-service");
      const header = el("div", "selected-service-header");
      const title = el("div");
      title.append(el("span", "custom-tag", "CUSTOM SERVICE"), el("h4", "", item.name || "New custom service"));
      const remove = el("button", "remove-service", "Remove");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove custom service ${item.name || ""}`);
      remove.addEventListener("click", () => { state.custom = state.custom.filter(x => x !== item); markDirty(); renderSelected(); updateSummary(); });
      header.append(title, remove);
      const body = el("div", "selected-service-body");
      body.append(makeField(item, "name", "Service name", {required: true, placeholder: "Name of the service", onChange: value => { title.querySelector("h4").textContent = value || "New custom service"; }}));
      body.append(makeField(item, "category", "Engagement type", {options: [{id: "on_demand", label: "On-demand project"}, {id: "recurring", label: "Recurring engagement"}], required: true, onChange: value => { item.model = customModels(value)[0].id; item.hours = 40; renderSelected(); }}));
      body.append(priceFields(item, customModels(item.category)));
      body.append(makeField(item, "scope", "Scope and deliverables", {required: true, multiline: true, placeholder: "State exactly what Scribe Align will provide and when the work is complete."}));
      body.append(makeField(item, "responsibilities", "Client responsibilities", {required: true, multiline: true, placeholder: "Specify information, access, approvals and cooperation the client must provide."}));
      body.append(makeField(item, "exclusions", "Limitations and exclusions", {required: true, multiline: true, placeholder: "Define work outside scope, third-party charges and any specific limitations."}));
      card.append(header, body);
      customContainer.append(card);
    });
  }

  function allSelected() {
    return [...state.selected.values()].map(item => ({...item, name: item.id === "website_development" ? (item.website_kind === "custom" ? "Custom Website Development" : "WordPress Website Development") : state.catalog.find(spec => spec.id === item.id)?.name || item.id})).concat(state.custom);
  }
  function shortPrice(item) {
    if (item.model === "rate_card") return `${money(item.government_rate)} / ${money(item.commercial_rate)}`;
    if (!numeric(item.rate)) return "Rate needed";
    if (item.model === "percentage") return `${numeric(item.rate)}%`;
    if (item.model === "unit") return `${money(item.rate)} / unit`;
    if (["fixed", "consultancy"].includes(item.model)) return `${money(item.rate)} / mo`;
    if (["fte", "pte"].includes(item.model)) return `${moneyCents(priceCents(item.rate, item.quantity))} / mo`;
    return `${moneyCents(priceCents(item.rate, item.quantity))} total`;
  }
  function entityState() { return (state.config?.entities || []).find(e => e.name === $("entity").value)?.governing_state || ""; }
  function chosenDocuments() { return [["generate-baa", "baa", "BAA"], ["generate-services", "services", "Services"], ["generate-nda", "nda", "NDA"]].filter(([id]) => $(id).checked); }
  function updateSummary() {
    const useBaa = $("generate-baa").checked, useServices = $("generate-services").checked;
    const selected = useServices ? allSelected() : [];
    document.querySelector(".summary-entity").textContent = $("entity").value;
    $("summary-client").textContent = $("client_name").value.trim() || "New client";
    $("folder-client").textContent = $("client_name").value.trim() || "Client legal name";
    $("summary-date").textContent = dateLabel($("effective_date").value);
    if (useBaa && $("separate-baa-date").checked) $("summary-date").textContent += ` · BAA ${dateLabel($("baa_effective_date").value)}`;
    const chosen = chosenDocuments();
    $("summary-documents").textContent = !chosen.length ? "None selected" : chosen.length === 1 ? `${chosen[0][2]} only` : chosen.map(x => x[2]).join(" + ");
    $("summary-count").textContent = String(selected.length);
    $("selected-count").textContent = `${state.selected.size + state.custom.length} selected`;
    $("generate-label").textContent = chosen.length > 1 ? "Generate agreements" : "Generate agreement";
    $("folder-baa").hidden = !useBaa;
    $("folder-services").hidden = !useServices;
    $("folder-nda").hidden = !$("generate-nda").checked;
    const list = $("summary-service-list");
    list.replaceChildren();
    selected.forEach(item => { const row = el("div", "summary-service"); row.append(el("span", "", item.name || "Custom service"), el("strong", "", shortPrice(item))); list.append(row); });
    const totals = $("pricing-summary");
    totals.replaceChildren();
    let monthly = 0n, project = 0n, variable = false;
    selected.forEach(item => {
      if (["fte", "pte"].includes(item.model)) monthly += priceCents(item.rate, item.quantity);
      else if (["fixed", "consultancy"].includes(item.model)) monthly += priceCents(item.rate);
      else if (["one_time", "two_installments"].includes(item.model)) project += priceCents(item.rate, item.quantity);
      else variable = true;
    });
    if (monthly) { const row = el("div"); row.append(el("span", "", "Fixed monthly charges"), el("strong", "", moneyCents(monthly))); totals.append(row); }
    if (project) { const row = el("div"); row.append(el("span", "", "Project fees"), el("strong", "", moneyCents(project))); totals.append(row); }
    if (variable) totals.append(el("p", "", "Percentage and per-unit fees are calculated separately from actual collections or completed units."));
    totals.hidden = !totals.childElementCount;
  }

  function documentSelectionChanged() {
    const serviceOn = $("generate-services").checked;
    $("services-panel").hidden = !serviceOn;
    $("services-panel").querySelectorAll("input,select,textarea,button").forEach(control => { control.disabled = !serviceOn; });
    $("baa-date-options").hidden = !$("generate-baa").checked;
    $("nda-options").hidden = !$("generate-nda").checked;
    $("nda-options").querySelectorAll("input,textarea").forEach(control => { control.disabled = !$("generate-nda").checked; });
    const separate = $("generate-baa").checked && $("separate-baa-date").checked;
    $("baa-date-field").hidden = !separate;
    $("baa_effective_date").required = separate;
    $("baa_effective_date").disabled = !separate;
    updateSummary();
  }
  function payload() {
    const data = {id: state.clientId, entity: $("entity").value};
    plainFields.forEach(key => { data[key] = $(key).value.trim(); });
    data.baa_effective_date = $("separate-baa-date").checked ? data.baa_effective_date : data.effective_date;
    data.document_types = [];
    if ($("generate-baa").checked) data.document_types.push("baa");
    if ($("generate-services").checked) data.document_types.push("services");
    if ($("generate-nda").checked) data.document_types.push("nda");
    data.services = [...state.selected.values()].map(item => ({...item}));
    data.include_signature = $("include-signature").checked;
    data.custom_services = state.custom.map(({_key, ...item}) => item);
    return data;
  }
  function validate(draft = false) {
    if (draft) {
      if (!$("client_name").value.trim()) { announce("Enter the client’s legal name before saving a draft.", true); $("client_name").focus(); return false; }
      return true;
    }
    form.classList.add("was-validated");
    if (!chosenDocuments().length) { announce("Select the Business Associate Agreement, the Services Agreement, the Mutual NDA, or a combination.", true); window.studioNavigate?.("documents-panel"); $("generate-baa").focus(); return false; }
    if ($("generate-services").checked && !state.selected.size && !state.custom.length) { announce("Select at least one service for the Services Agreement.", true); window.studioNavigate?.("services-panel"); $("services-panel").scrollIntoView({behavior: "smooth", block: "start"}); $("service-search").focus({preventScroll: true}); return false; }
    if (!form.checkValidity()) {
      const invalid = [...form.querySelectorAll("input,select,textarea")].find(control => !control.disabled && !control.validity.valid);
      if (invalid) {
        window.studioNavigate?.(invalid.closest("section.panel")?.id);
        invalid.closest("details")?.setAttribute("open", "");
        const label = document.querySelector(`label[for="${invalid.id}"]`);
        announce(`Please complete or correct ${label?.textContent.replace(/\*/g, "").trim().toLowerCase() || "the highlighted field"}. ${invalid.validationMessage}`, true);
        invalid.focus();
        invalid.reportValidity();
      }
      return false;
    }
    return true;
  }

  function busy(value, label) {
    state.busy = value;
    if (value) {
      state.disabledControls.clear();
      form.querySelectorAll("input,select,textarea,button").forEach(control => { state.disabledControls.set(control, control.disabled); control.disabled = true; });
    } else {
      state.disabledControls.forEach((wasDisabled, control) => { control.disabled = wasDisabled; });
      state.disabledControls.clear();
      documentSelectionChanged();
    }
    form.setAttribute("aria-busy", String(value));
    if (value && label) $("generate-label").textContent = label;
    else updateSummary();
  }

  async function refreshClients(selectId) {
    const result = await api("/api/clients");
    const dropdown = $("saved-client");
    const first = el("option", "", "Select a saved client…");
    first.value = "";
    dropdown.replaceChildren(first);
    (result.clients || []).forEach(client => { const option = el("option", "", client.client_name); option.value = client.id; dropdown.append(option); });
    dropdown.value = selectId || state.clientId || "";
  }

  function applyClient(data, resetServices = false) {
    const allowed = state.config.entities || [];
    $("entity").value = allowed.some(e => e.name === data.entity) ? data.entity : allowed[0]?.name || "";
    $("include-signature").checked = data.include_signature !== false;
    window.updateCompanyEntity?.(false);
    plainFields.forEach(key => { if (data[key] !== undefined) $(key).value = data[key]; });
    if (!$("governing_state").value.trim()) $("governing_state").value = entityState();
    state.clientId = data.id || "";
    state.selected.clear();
    state.custom = [];
    if (!resetServices) {
      (data.services || []).forEach(item => { const spec = state.catalog.find(x => x.id === item.id); if (spec) state.selected.set(item.id, normalizeSelection(item, spec)); });
      state.custom = (data.custom_services || []).map(item => ({quantity: 1, hours: 40, rate: "", unit: "", milestone1: "Execution and commencement", milestone2: "Delivery of the agreed final work product", installment_percent: 50, ...item, _key: `custom-${++state.customCounter}`}));
    }
    const types = resetServices ? ["services"] : data.document_types || ["baa", "services"];
    $("generate-baa").checked = types.includes("baa");
    $("generate-services").checked = types.includes("services");
    $("generate-nda").checked = types.includes("nda");
    if (resetServices) { $("effective_date").value = today(); $("baa_effective_date").value = today(); $("special_terms").value = ""; }
    $("separate-baa-date").checked = !!data.baa_effective_date && data.baa_effective_date !== data.effective_date && !resetServices;
    form.classList.remove("was-validated");
    $("generation-results").hidden = true;
    renderCatalog(); renderSelected(); documentSelectionChanged();
    state.dirty = false;
  }

  function resetClient() {
    const defaults = state.config.defaults || {};
    const output = $("output_root").value || state.config.output_root;
    form.reset();
    const data = {...defaults, client_name: "", address: "", client_signatory: "", client_title: "", client_email: "", effective_date: today(), baa_effective_date: today(), special_terms: "", governing_state: entityState(), output_root: output, document_types: ["baa", "services"]};
    applyClient(data);
    $("saved-client").value = "";
    clearMessages();
    $("client_name").focus();
  }

  function renderResults(result) {
    $("generation-results").hidden = false;
    $("send-signature").hidden = !result.contract_id;
    $("send-signature").onclick = () => window.esignFromContract?.(result.contract_id);
    $("result-id").textContent = `Reference ${result.contract_id}`;
    $("result-folder").textContent = result.folder;
    const files = $("result-files");
    files.replaceChildren();
    (result.files || []).forEach(file => {
      // Only local file endpoints returned by our service can become links.
      if (typeof file.url !== "string" || !/^\/files\/[A-Za-z0-9_-]+$/.test(file.url)) return;
      const link = el("a", "result-file");
      link.href = file.url;
      link.target = "_blank";
      link.rel = "noopener";
      const kind = {baa: "Business Associate Agreement", services: "Services Agreement", nda: "Mutual NDA"}[file.agreement] || (/Business Associate/i.test(file.name) ? "Business Associate Agreement" : "Services Agreement");
      link.append(el("span", "", String(file.type || "FILE").toUpperCase()), el("strong", "", kind));
      link.setAttribute("aria-label", `Open ${kind} ${file.type || "file"}`);
      files.append(link);
    });
    const warnings = $("result-warnings");
    warnings.replaceChildren();
    (result.warnings || []).forEach(warning => warnings.append(el("p", "result-warning", warning)));
  }

  function previewBlocks(blocks, effective) {
    const container = el("div");
    (blocks || []).forEach(block => {
      if (block.type === "table") {
        const table = el("table");
        if (block.headers?.length) { const head = el("thead"), row = el("tr"); block.headers.forEach(text => row.append(el("th", "", text))); head.append(row); table.append(head); }
        const body = el("tbody");
        (block.rows || []).forEach(cells => { const row = el("tr"); cells.forEach(cell => row.append(el("td", "", cell))); body.append(row); });
        table.append(body); container.append(table);
      } else if (["pagebreak", "page_break", "signature_page", "signature"].includes(block.type)) {
        container.append(el("div", "preview-pagebreak"));
        if (block.text) container.append(el("p", "", block.text));
        if (block.type === "signature") {
          const signatures = el("table");
          const head = el("thead"), headings = el("tr");
          headings.append(el("th", "", $("entity").value), el("th", "", $("client_name").value));
          head.append(headings);
          const body = el("tbody"), row = el("tr");
          row.append(el("td", "", `Signature: ____________________\n${$("signatory-name").textContent}\n${$("signatory-title").textContent.split(" · ")[0]}\nDate: ${dateLabel(effective)}`), el("td", "", `Signature: ____________________\nName: ${$("client_signatory").value || "____________________"}\nTitle: ${$("client_title").value || "____________________"}\nDate: ____________________`));
          body.append(row); signatures.append(head, body); container.append(signatures);
        }
      } else if (["bullet", "li"].includes(block.type)) {
        const list = container.lastElementChild?.tagName === "UL" ? container.lastElementChild : el("ul");
        list.append(el("li", "", block.text));
        if (!list.parentElement) container.append(list);
      } else {
        const tag = ["h1", "h2", "h3"].includes(block.type) ? block.type : block.type === "title" ? "h1" : "p";
        container.append(el(tag, "", block.text || ""));
      }
    });
    return container;
  }

  function showPreview(documents) {
    const tabs = $("preview-tabs"), content = $("preview-content");
    tabs.replaceChildren(); content.replaceChildren();
    documents.forEach((doc, index) => {
      const tab = el("button", "", doc.title);
      tab.type = "button";
      tab.id = `preview-tab-${index}`;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(index === 0));
      tab.setAttribute("aria-controls", "preview-content");
      tab.tabIndex = index === 0 ? 0 : -1;
      const select = () => {
        [...tabs.children].forEach(button => { button.setAttribute("aria-selected", String(button === tab)); button.tabIndex = button === tab ? 0 : -1; });
        content.replaceChildren(previewBlocks(doc.blocks, doc.type === "baa" && $("separate-baa-date").checked ? $("baa_effective_date").value : $("effective_date").value));
        content.scrollTop = 0;
        content.setAttribute("aria-labelledby", tab.id);
      };
      tab.addEventListener("click", select);
      tab.addEventListener("keydown", event => {
        if (["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          const children = [...tabs.children];
          const next = event.key === "Home" ? 0 : event.key === "End" ? children.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + children.length) % children.length;
          children[next].click(); children[next].focus();
        }
      });
      tabs.append(tab);
      if (index === 0) select();
    });
    content.setAttribute("role", "tabpanel");
    content.tabIndex = 0;
    $("preview-dialog").showModal();
  }

  form.addEventListener("input", () => { markDirty(); updateSummary(); });
  form.addEventListener("change", markDirty);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (state.busy || !validate()) return;
    clearMessages(); busy(true, "Creating Word & PDF…");
    try {
      const queued = await api("/api/generate", {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify(payload())});
      const result = await window.waitForGeneration(queued.job_id, message => { $("generate-label").textContent = message; });
      state.clientId = result.id || state.clientId;
      state.dirty = false;
      renderResults(result);
      announce("Your agreement package has been created. Open the Word and PDF files in the results panel.");
      await refreshClients(state.clientId).catch(() => {});
      $("generation-results").scrollIntoView({behavior: "smooth", block: "center"});
    } catch (error) { announce(error.message, true); }
    finally { busy(false); }
  });
  $("preview-button").addEventListener("click", async () => {
    if (state.busy || !validate()) return;
    clearMessages(); busy(true, "Preparing preview…");
    try { const result = await api("/api/preview", {method: "POST", body: JSON.stringify(payload())}); showPreview(result.documents || []); }
    catch (error) { announce(error.message, true); }
    finally { busy(false); }
  });
  $("save-client").addEventListener("click", async () => {
    if (state.busy || !validate(true)) return;
    clearMessages(); busy(true, "Saving draft…");
    try {
      const result = await api("/api/clients", {method: "POST", body: JSON.stringify(payload())});
      state.clientId = result.id; state.dirty = false;
      await refreshClients(result.id);
      announce(`Draft saved for ${$("client_name").value.trim()}. You can reopen it from the saved client list.`);
    } catch (error) { announce(error.message, true); }
    finally { busy(false); }
  });
  $("saved-client").addEventListener("change", async () => {
    const id = $("saved-client").value;
    if (!id) return;
    if (state.dirty && !window.confirm("Open this saved client and replace the unsaved changes in this form?")) { $("saved-client").value = state.clientId; return; }
    const reset = $("clear-services-on-load").checked;
    clearMessages(); busy(true, "Opening draft…");
    try { const data = await api(`/api/clients/${encodeURIComponent(id)}`); applyClient(data, reset); announce(reset ? "Client details loaded. Choose the services for this new standalone agreement." : "Saved client and agreement details loaded. Review the effective date before generating."); }
    catch (error) { announce(error.message, true); }
    finally { busy(false); }
  });
  $("new-client").addEventListener("click", () => { if (!state.dirty || window.confirm("Start a new client and clear the unsaved changes in this form?")) resetClient(); });
  $("choose-folder").addEventListener("click", () => window.chooseOutputFolder());
  $("today-button").addEventListener("click", () => { $("effective_date").value = today(); markDirty(); updateSummary(); });
  $("baa-today-button").addEventListener("click", () => { $("baa_effective_date").value = today(); markDirty(); updateSummary(); });
  ["generate-baa", "generate-services", "generate-nda", "separate-baa-date"].forEach(id => $(id).addEventListener("change", documentSelectionChanged));
  $("service-search").addEventListener("input", renderCatalog);
  document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach(other => other.setAttribute("aria-pressed", String(other === button)));
    renderCatalog();
  }));
  $("add-custom").addEventListener("click", () => {
    const item = {_key: `custom-${++state.customCounter}`, name: "", category: "on_demand", model: "one_time", rate: "", quantity: 1, hours: 40, unit: "", details: "", scope: "", responsibilities: "", exclusions: "", milestone1: "Execution and commencement", milestone2: "Delivery of the agreed final work product", installment_percent: 50};
    state.custom.push(item); markDirty(); renderSelected(); updateSummary();
    $(`${item._key}-name`).focus();
  });
  $("help-button").addEventListener("click", () => $("help-dialog").showModal());
  document.querySelectorAll(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  document.querySelectorAll("dialog").forEach(dialog => dialog.addEventListener("click", event => { if (event.target === dialog) { const box = dialog.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close(); } }));
  window.addEventListener("beforeunload", event => { if (state.dirty || state.busy) { event.preventDefault(); event.returnValue = ""; } });

  async function initialize() {
    if (location.protocol === "file:") {
      $("loading").hidden = true;
      announce("Open “Start TFSC Compliance.cmd” in the application folder to launch this workspace. The HTML file needs the local service to create Word, PDF and client folders.", true);
      return;
    }
    try {
      const config = await api("/api/config");
      if (!Array.isArray(config.catalog) || !config.catalog.length) throw new Error("The service catalog could not be loaded.");
      state.config = config; state.catalog = config.catalog;
      const companySelect=$("entity");companySelect.replaceChildren();
      config.entities.forEach(entity=>{const option=el("option","",entity.name);option.value=entity.name;companySelect.append(option);});
      window.portalReady?.(config);
      Object.entries(config.defaults || {}).forEach(([key, value]) => { if ($(key)) $(key).value = value; });
      (config.states || []).forEach(name => { const option = el("option"); option.value = name; $("us-states").append(option); });
      $("governing_state").value = entityState();
      $("output_root").value = config.output_root || "";
      $("effective_date").value = today(); $("baa_effective_date").value = today();
      $("app-version").textContent = `${config.local === false ? "Hosted edition" : "Local edition"} · v${config.version || "1.0"}`;
      renderCatalog(); renderSelected(); documentSelectionChanged();
      await refreshClients();
      form.hidden = (document.body.dataset.view || "builder") !== "builder"; state.dirty = false;
      const linkedClient=new URLSearchParams(location.search).get('client');
      if(linkedClient&&/^[a-f0-9]{32}$/.test(linkedClient))await window.openClientDraft(linkedClient);
    } catch (error) { announce(error.message, true); }
    finally { $("loading").hidden = true; }
  }
  window.openClientDraft=async id=>{if(state.dirty&&!confirm("Open this client and replace unsaved changes?"))return;const d=await api(`/api/clients/${encodeURIComponent(id)}`);applyClient(d);window.showBuilder?.();};
  initialize();
})();
