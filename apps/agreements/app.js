"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const form = $("contract-form");
  const state = {config: null, catalog: [], selected: new Map(), custom: [], clientId: "", filter: "all", busy: false, dirty: false, customCounter: 0, disabledControls: new Map()};
  const modelLabels = {percentage: "Percentage of net collections", fte: "FTE per month", pte: "PTE per month", unit: "Per completed unit", fixed: "Fixed monthly fee", consultancy: "Monthly consultancy fee", one_time: "One project invoice", two_installments: "Two project installments"};
  const plainFields = ["client_name", "address", "client_signatory", "client_title", "client_email", "effective_date", "baa_effective_date", "governing_state", "notice_days", "payment_days", "cure_days", "special_terms", "nda_purpose", "nda_term_years", "nda_survival_years", "output_root"];
  const serviceHints = {
    medical_billing: "Identify providers, payers, practice systems and dates of service. Define any excluded accounts or collections.",
    wc_collection: "Identify assigned workers’ compensation accounts, jurisdictions and retained counsel, if applicable.",
    personal_injury_collection: "Identify assigned personal injury medical accounts, jurisdictions, authorized attorney/insurer contacts and any letters of protection. For FTE pricing, specify staffing and coverage hours.",
    medical_coding: "Define the coding review scope, sample size, specialties, deliverables and review frequency.",
    prior_auth: "Specify authorization or benefit verification tasks, providers, payers and turnaround expectations.",
    virtual_assistant: "Specify administrative duties, schedule, time zone, systems and communication channels.",
    patient_help_desk: "Specify call hours and time zone, expected call volume, languages, phone system and escalation contacts. For per-call pricing, state what counts as a completed call.",
    medical_scribe: "Specify live or offline work, specialties, schedule, time zone and documentation workflow.",
    ar_denials: "Define assigned accounts, aging range, dates of service, systems and reporting expectations.",
    rcm_consultancy: "Define the assessment areas, work schedule, deliverables and implementation support.",
    digital_marketing: "Specify channels, deliverables, approval workflow and budget. State whether media spend is separate.",
    telemarketing: "Specify campaign, target audience, approved scripts, calling schedule and consent records.",
    credentialing: "Identify providers, payer panels, states, application types and completion milestones.",
    demographics_updates: "List the providers, payers and the updates needed (address, contact, tax and ownership, group roster or CAQH ProView), and any payer deadline.",
    state_licensing: "Identify applicants, jurisdictions, license types, application scope and completion milestones.",
    compliance_training: "Specify training topics, audience, session format, materials and delivery dates.",
    website_development: "Define pages, features, content, revision rounds, acceptance criteria and handover.",
    consultancy: "Define the question, project scope, deliverables and completion date.",
    mips_consultancy: "Specify performance year, reporting entity, measures, systems and agreed deliverables.",
    coding_consultancy: "Specify specialties, sample size, records reviewed, reporting and education deliverables.",
    other: "Required: define the exact service, deliverables, responsibilities and completion criteria."
  };
  // The line under a service's name in the catalogue (otherwise its engagement type), and the services marked NEW.
  const serviceLines = {patient_help_desk: "Recurring engagement · patient billing calls, payment plans, insurance follow-up",
    demographics_updates: "Credentialing add-on · also on its own"};
  const newServices = new Set(["patient_help_desk", "demographics_updates"]);
  const ADD_ON = {credentialing: "demographics_updates"};   // offered inside the Credentialing card
  const SERVICE_TEXT_LIMIT = 6000;                           // server.SERVICE_TEXT_LIMIT

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
    return {id: spec.id, model, rate: "", government_rate: 150, commercial_rate: 150, website_kind: "wordpress", website_prices: {}, quantity: 1, hours: model === "pte" ? 20 : 40, unit: spec.unit || "", details: "", milestone1: "Execution and commencement", milestone2: "Delivery of the agreed final work product", installment_percent: 50};
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
      if (newServices.has(spec.id)) text.append(el("em", "new-badge", "NEW"));
      text.append(el("small", "", serviceLines[spec.id] || (spec.category === "recurring" ? "Recurring engagement" : "On-demand project")));
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
      const addOn = ADD_ON[spec.id] && state.catalog.find(s => s.id === ADD_ON[spec.id]);
      if (addOn && state.selected.has(addOn.id)) title.querySelector("h4").append(el("span", "addon-chip", "+ Demographics Updates"));
      const remove = el("button", "remove-service", "Remove");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${spec.name}`);
      remove.addEventListener("click", () => { state.selected.delete(spec.id); markDirty(); renderCatalog(); renderSelected(); updateSummary(); });
      header.append(title, remove);
      const body = el("div", "selected-service-body");
      if (addOn) body.append(addOnOption(addOn, spec.id));
      body.append(priceFields(item, spec.models), makeField(item, "details", "Service parameters & agreed deliverables", {multiline: true, maxLength: SERVICE_TEXT_LIMIT, required: spec.id === "other", placeholder: serviceHints[spec.id] || "Describe the service parameters for this engagement.", note: "These details are added to this service’s standard scope and responsibilities."}));
      card.append(header, body);
      selected.append(card);
    });
    const customContainer = $("custom-services");
    customContainer.replaceChildren();
    if (state.custom.length) customContainer.append(formattingBanner());
    state.custom.forEach((item, index) => {
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
      const fields = el("div", "custom-fields"), print = printPreview(item, state.selected.size + index + 1);
      const text = {required: true, multiline: true, rows: 6, maxLength: SERVICE_TEXT_LIMIT, onChange: print.refresh};
      fields.append(makeField(item, "name", "Service name", {required: true, placeholder: "Name of the service", onChange: value => { title.querySelector("h4").textContent = value || "New custom service"; print.refresh(); }}));
      fields.append(makeField(item, "category", "Engagement type", {options: [{id: "on_demand", label: "On-demand project"}, {id: "recurring", label: "Recurring engagement"}], required: true, onChange: value => { item.model = customModels(value)[0].id; item.hours = 40; renderSelected(); }}));
      fields.append(priceFields(item, customModels(item.category)));
      fields.append(makeField(item, "scope", "Scope and deliverables", {...text, placeholder: "State exactly what Scribe Align will provide and when the work is complete. Paste it as it comes: bullets, numbering and line breaks print in the agreement’s own style."}));
      fields.append(makeField(item, "responsibilities", "Client responsibilities", {...text, placeholder: "Specify information, access, approvals and cooperation the client must provide."}));
      fields.append(makeField(item, "exclusions", "Limitations and exclusions", {...text, placeholder: "Define work outside scope, third-party charges and any specific limitations."}));
      body.classList.add("custom-editor");
      body.append(fields, print.node);
      card.append(header, body);
      customContainer.append(card);
    });
  }

  // "Add Demographics Updates" inside the Credentialing card: selects or removes the add-on service, which keeps its own
  // pricing card and prints as its own service, right after Credentialing.
  function addOnOption(spec, parentId) {
    const option = el("label", "addon-option");
    const box = el("input"); box.type = "checkbox"; box.checked = state.selected.has(spec.id); box.disabled = !$("generate-services").checked;
    box.addEventListener("change", () => {
      if (box.checked) {
        const entries = [...state.selected.entries()].filter(([id]) => id !== spec.id), at = entries.findIndex(([id]) => id === parentId);
        entries.splice(at < 0 ? entries.length : at + 1, 0, [spec.id, serviceDefaults(spec)]);
        state.selected = new Map(entries);
      } else state.selected.delete(spec.id);
      markDirty(); renderCatalog(); renderSelected(); updateSummary();
    });
    const text = el("span", "addon-text"), name = el("strong", "", "Add Demographics Updates");
    name.append(el("em", "new-badge", "NEW"));
    text.append(name, el("small", "", box.checked ? "Added · priced per payer update or as a project in its own card below" : "Payer updates of provider and practice details, CAQH ProView included"));
    option.append(box, text);
    return option;
  }

  function formattingBanner() {
    const banner = el("div", "format-banner");
    const text = el("p");
    text.append(el("strong", "", "Formatting is protected. "), document.createTextNode("Type or paste the custom service however it comes (from Word, an email or a PDF). It prints in the agreement’s own headings, bullets and numbering, exactly like the standard services."));
    banner.append(el("span", "format-banner-mark", "✓"), text);
    return banner;
  }

  // Blocks from format_guard (p, lead, bullet, number with label/style/start) as they print. Lists are grouped.
  function appendFormatted(container, block) {
    if (block.type === "bullet" || block.type === "number") {
      const tag = block.type === "bullet" ? "UL" : "OL", last = container.lastElementChild;
      const letter = block.type === "number" && block.style === "letter";
      let list = last && last.tagName === tag && !(block.type === "number" && block.start !== undefined) && last.classList.contains("letter") === letter ? last : null;
      if (!list) {
        list = el(tag.toLowerCase(), letter ? "letter" : "");
        if (block.type === "number" && block.start > 1) list.start = block.start;
        container.append(list);
      }
      const li = el("li");
      const text = String(block.text || "");
      if (block.label && text.startsWith(block.label)) li.append(el("strong", "", block.label), document.createTextNode(text.slice(block.label.length)));
      else li.textContent = text;
      list.append(li);
    } else container.append(el("p", block.type === "lead" ? "print-lead" : "", block.text || ""));
  }

  function printPreview(item, number) {
    const node = el("aside", "print-preview"), head = el("div", "print-head"), paper = el("div", "print-paper"), chips = el("ul", "print-chips");
    head.append(el("strong", "", "How it will print"), el("span", "", "live preview · Services Agreement"));
    node.append(head, paper, chips);
    node.setAttribute("aria-label", "How this custom service will print");
    const refresh = () => {
      const guard = window.TFSCFormat;
      paper.replaceChildren(); chips.replaceChildren();
      paper.append(el("h3", "", `1.${number} ${item.name || "New custom service"}`));
      const total = {bullets: 0, numbers: 0, leads: 0, labels: 0, joined: 0, spaces: 0, capitalised: 0};
      let lead = "", any = false;
      [["scope", ""], ["responsibilities", "Client Responsibilities"], ["exclusions", "Limitations and Exclusions"]].forEach(([key, heading]) => {
        const value = String(item[key] || "");
        if (!value.trim()) return;
        any = true;
        if (heading) paper.append(el("h4", "", heading));
        const result = guard ? guard.inspect(value) : {blocks: [{type: "p", text: value}], stats: {}};
        result.blocks.forEach(block => { appendFormatted(paper, block); if (block.type === "lead" && !lead) lead = block.text; });
        Object.keys(total).forEach(k => { total[k] += result.stats[k] || 0; });
      });
      if (!any) paper.append(el("p", "print-empty", "Type or paste the scope, responsibilities and exclusions to see them as they print."));
      const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
      const notes = [];
      if (total.bullets) notes.push(`${plural(total.bullets, "bullet", "bullets")} in the agreement’s style`);
      if (total.numbers) notes.push(plural(total.numbers, "numbered step", "numbered steps"));
      if (total.leads) notes.push(total.leads === 1 ? `“${lead.length > 40 ? lead.slice(0, 38) + "…:" : lead}” introduces the list` : `${total.leads} lead-ins kept with their lists`);
      if (total.labels) notes.push(`${plural(total.labels, "label", "labels")} in bold`);
      if (total.joined) notes.push(total.joined === 1 ? "Broken line joined" : `${total.joined} broken lines joined`);
      if (total.spaces) notes.push("Extra spaces removed");
      if (total.capitalised) notes.push("First letters capitalised");
      if (any) notes.push("Same font and spacing as the standard services");
      notes.forEach(text => chips.append(el("li", "", `✓ ${text}`)));
    };
    refresh();
    return {node, refresh};
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
  // A custom company with its own services offers those; every other company (built-in, or custom with none of its own)
  // offers the standard catalogue.
  window.setCompanyCatalog = function() {
    const entry = (state.config?.entities || []).find(e => e.name === $("entity").value);
    const next = (entry && Array.isArray(entry.services) && entry.services.length) ? entry.services : (state.config?.catalog || []);
    state.catalog = next;
    for (const id of [...state.selected.keys()]) if (!next.some(s => s.id === id)) state.selected.delete(id);
    if ($("catalog-total")) $("catalog-total").textContent = String(next.length);
    renderLock(entry); applyTheme(entry);
    renderCatalog(); renderSelected(); updateSummary();
  };

  // ------------------------------------------------------------------ company picker, locked company and company colours
  let picked = "";           // the company chosen for the agreement on this page
  let bypassPicker = false;  // a saved client opened from the Clients page brings its own company
  const entities = () => state.config?.entities || [];
  const currentEntity = () => entities().find(e => e.name === $("entity").value);
  function hexRgb(hex) { const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim()); const n = m ? parseInt(m[1], 16) : 0x1D4277; return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
  function rgbHex(rgb) { return "#" + rgb.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join(""); }
  function mix(a, b, t) { const x = hexRgb(a), y = hexRgb(b); return rgbHex(x.map((v, i) => v + (y[i] - v) * t)); }
  function luminance(hex) { const [r, g, b] = hexRgb(hex).map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * r + .7152 * g + .0722 * b; }
  function deepen(hex, max) { let c = hex; for (let i = 0; i < 14 && luminance(c) > max; i++) c = mix(c, "#000000", .14); return c; }
  // Any company's brand colours, adjusted so white text on them stays readable (works for companies added later too).
  function themeOf(e) {
    const c = deepen(e?.color || "#1D4277", .16), a = e?.accent || "#189BD2";
    const [ar, ag, ab] = hexRgb(a);
    return {c, a, d: deepen(e?.dark || mix(c, "#000000", .55), .035), e: deepen(mix(c, a, .35), .2),
      l: luminance(a) > .45 ? a : mix(a, "#ffffff", .55), g: `rgba(${ar},${ag},${ab},.30)`, soft: mix(c, "#ffffff", .9), line: mix(c, "#ffffff", .75)};
  }
  window.companyTheme = themeOf;
  function applyTheme(e) {
    const t = themeOf(e), view = $("builder-view"), card = document.querySelector(".summary-card");
    const vars = {"--primary": t.c, "--primary-2": mix(t.c, "#000000", .18), "--primary-soft": t.soft, "--primary-line": t.line,
      "--co-c": t.c, "--co-a": t.a, "--co-d": t.d, "--co-e": t.e, "--co-l": t.l, "--co-g": t.g};
    Object.entries(vars).forEach(([k, v]) => view.style.setProperty(k, v));
    card.classList.add("themed");
    $("sc-plate").hidden = !e?.logo;
    if (e?.logo) $("sc-logo").src = e.logo;
  }
  function renderLock(e) {
    $("company-lock").hidden = !e; $("entity").hidden = !!e; $("entity-signatory").hidden = !!e;
    if (!e) return;
    $("lock-logo").src = e.logo || ""; $("lock-logo").alt = e.short || e.name;
    $("lock-name").textContent = e.name;
    $("lock-meta").textContent = (e.signatory ? `Authorized signatory: ${e.signatory}, ${e.title}` : "Signatory not set yet: a Super Admin adds it in Admin › Company settings before agreements can be generated") + ` · ${e.governing_state} law${e.address ? ` · ${e.address}` : ""}`;
    $("lock-meta").title = $("lock-meta").textContent; $("lock-name").title = e.name;
    $("change-company").hidden = entities().length < 2;
  }
  function monogram(e) { return el("span", "cp-mono", (e.short || e.name).split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join("").toUpperCase()); }
  function tile(e, current) {
    const t = themeOf(e), b = el("button", "cp-tile"); b.type = "button"; b.setAttribute("role", "listitem");
    [["--c", t.c], ["--a", t.a], ["--glow", t.g]].forEach(([k, v]) => b.style.setProperty(k, v));
    if (e.name === current) b.classList.add("is-current");
    const card = el("span", "cp-card");
    if (e.logo) { const img = el("img"); img.alt = ""; img.src = e.logo; img.addEventListener("error", () => img.replaceWith(monogram(e))); card.append(img); }
    else card.append(monogram(e));
    card.append(el("span", "cp-band"));
    const label = e.short || e.name, name = el("span", "cp-name", label); name.title = e.name;
    if (label.length > 20) name.classList.add("long");
    const meta = el("span", "cp-meta", `${e.governing_state} law · ${e.signatory || "Signatory not set"}`); meta.title = meta.textContent;
    b.append(card, name, meta);
    b.setAttribute("aria-label", `${e.name}: ${e.governing_state} law, ${e.signatory ? `signed by ${e.signatory}` : "signatory not set"}`);
    return b;
  }
  // The full-page "which company?" screen. Tiles size themselves to the number of companies and to the screen.
  window.openCompanyPicker = function({purpose = "create", current = "", onPick, onCancel} = {}) {
    const list = entities(), box = $("picker-tiles"), picker = $("company-picker");
    box.replaceChildren();
    box.style.setProperty("--tile", list.length <= 4 ? "196px" : list.length <= 6 ? "172px" : list.length <= 8 ? "152px" : "136px");
    const send = purpose === "send";
    $("picker-eyebrow").textContent = send ? "SEND AN ENVELOPE" : "CREATE AGREEMENT";
    $("picker-title").textContent = send ? "Which company is sending this envelope?" : "Which company is this agreement for?";
    $("picker-sub").textContent = send ? "Choose the company the documents come from. Its logo, colours and mailbox are used for every email the recipients receive."
      : "Choose the company the agreement is issued under. Its letterhead, signatory, address, governing law and services are applied automatically.";
    const close = () => { picker.hidden = true; document.body.classList.remove("picking"); document.removeEventListener("keydown", keys); };
    function keys(event) {
      if (event.key === "Escape") { event.preventDefault(); close(); onCancel?.(); return; }
      if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(event.key)) return;
      const tiles = [...box.querySelectorAll(".cp-tile")], at = tiles.indexOf(document.activeElement);
      const step = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1;
      tiles[(at + step + tiles.length) % tiles.length]?.focus(); event.preventDefault();
    }
    list.forEach(e => { const t = tile(e, current); t.addEventListener("click", () => { close(); onPick?.(e.name); }); box.append(t); });
    $("picker-back").onclick = () => { close(); window.portalRoute("#/home"); };
    picker.hidden = false; picker.scrollTop = 0; document.body.classList.add("picking"); document.addEventListener("keydown", keys);
    // Only the company already in use is highlighted; otherwise nothing looks pre-selected (arrow keys still work).
    const cur = box.querySelector(".is-current"); if (cur) cur.focus({preventScroll: true}); else { picker.tabIndex = -1; picker.focus({preventScroll: true}); }
  };
  // Only ask before replacing the form when it holds something worth keeping (choosing a company alone is not).
  const unsavedWork = () => state.dirty && !!($("client_name").value.trim() || $("address").value.trim() || state.selected.size || $("special_terms").value.trim());
  function useCompany(name) {
    const before = [...state.selected.values()].map(x => state.catalog.find(s => s.id === x.id)?.name || x.id);
    const changed = $("entity").value !== name;
    picked = name;
    if (!changed) return;
    $("entity").value = name; window.updateCompanyEntity?.(true); markDirty();
    const after = new Set([...state.selected.values()].map(x => state.catalog.find(s => s.id === x.id)?.name || x.id));
    const removed = before.filter(n => !after.has(n));
    announce(`This agreement is now for ${name}.${removed.length ? ` Removed services it does not offer: ${removed.join(", ")}.` : ""}`);
  }
  // Called by the router each time Create agreement opens; `from` is the screen it came from.
  window.createEnter = function(from) {
    const list = entities();
    if (!list.length) return;
    if (bypassPicker) { bypassPicker = false; picked = $("entity").value; return; }
    if (list.length === 1) { useCompany(list[0].name); return; }
    if (from === "builder" && picked) return;
    window.openCompanyPicker({purpose: "create", current: picked, onPick: useCompany, onCancel: () => { if (!picked) window.portalRoute("#/home"); }});
  };
  $("change-company").addEventListener("click", () => {
    const e = currentEntity(); $("change-company-name").textContent = e?.name || "";
    $("change-company-keep").textContent = `No, keep ${e?.short || "this company"}`;
    $("change-company-dialog").showModal();
  });
  $("change-company-keep").addEventListener("click", () => $("change-company-dialog").close());
  $("change-company-yes").addEventListener("click", () => {
    $("change-company-dialog").close();
    window.openCompanyPicker({purpose: "create", current: $("entity").value, onPick: useCompany});
  });
  function testChanged() {
    const on = $("test-agreement").checked;
    document.querySelector(".summary-card").classList.toggle("is-test", on);
    $("test-note").textContent = on ? "Watermarked TEST. Kept out of reports, the Agreement log, Home and client counts." : "Turn on for practice runs. Marked TEST and kept out of reports.";
    updateSummary();
  }
  $("test-agreement").addEventListener("change", () => { markDirty(); testChanged(); });
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
    $("generate-label").textContent = `Generate ${$("test-agreement").checked ? "test " : ""}${chosen.length > 1 ? "agreements" : "agreement"}`;
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
    data.test = $("test-agreement").checked;
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
    (result.clients || []).forEach(client => { const option = el("option", "", client.client_number ? `${client.client_number} · ${client.client_name}` : client.client_name); option.value = client.id; dropdown.append(option); });
    dropdown.value = selectId || state.clientId || "";
  }

  // keepEntity: the company chosen for this agreement stays, even if the saved client was drafted for another one.
  function applyClient(data, resetServices = false, keepEntity = false) {
    const allowed = state.config.entities || [];
    if (!keepEntity || !allowed.some(e => e.name === $("entity").value)) $("entity").value = allowed.some(e => e.name === data.entity) ? data.entity : allowed[0]?.name || "";
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
    $("test-agreement").checked = !!data.test && !resetServices; testChanged();
    $("separate-baa-date").checked = !!data.baa_effective_date && data.baa_effective_date !== data.effective_date && !resetServices;
    form.classList.remove("was-validated");
    $("generation-results").hidden = true;
    renderCatalog(); renderSelected(); documentSelectionChanged();
    state.dirty = false;
  }

  function resetClient() {
    const defaults = state.config.defaults || {};
    const output = $("output_root").value || state.config.output_root, entity = $("entity").value;
    form.reset();
    const data = {...defaults, entity, client_name: "", address: "", client_signatory: "", client_title: "", client_email: "", effective_date: today(), baa_effective_date: today(), special_terms: "", governing_state: (state.config.entities || []).find(e => e.name === entity)?.governing_state || "", output_root: output, document_types: ["baa", "services"]};
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
      } else if (["bullet", "number", "lead"].includes(block.type)) {
        appendFormatted(container, block);
      } else if (block.type === "li") {
        const list = container.lastElementChild?.tagName === "UL" ? container.lastElementChild : el("ul");
        list.append(el("li", "", block.text));
        if (!list.parentElement) container.append(list);
      } else if (block.runs?.length && block.type === "p") {
        const p = el("p");
        block.runs.forEach(run => p.append(run.bold ? el("strong", "", run.text) : document.createTextNode(run.text)));
        container.append(p);
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
    if (unsavedWork() && !window.confirm("Open this saved client and replace the unsaved changes in this form?")) { $("saved-client").value = state.clientId; return; }
    const reset = $("clear-services-on-load").checked;
    clearMessages(); busy(true, "Opening draft…");
    try { const data = await api(`/api/clients/${encodeURIComponent(id)}`); applyClient(data, reset, !!picked); announce(reset ? "Client details loaded. Choose the services for this new standalone agreement." : "Saved client and agreement details loaded. Review the effective date before generating."); }
    catch (error) { announce(error.message, true); }
    finally { busy(false); }
  });
  $("new-client").addEventListener("click", () => { if (!unsavedWork() || window.confirm("Start a new client and clear the unsaved changes in this form?")) resetClient(); });
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

  // The portal's screens live in scripts that load after this one (portal.js, esign.js, ... admin.js). DOMContentLoaded
  // fires only after every deferred script has run, so the screens start once all of them exist, however fast /api/config
  // answers (otherwise a reload of, say, Company settings could find its screen not loaded yet and stay empty).
  const scriptsLoaded = document.readyState === "complete" ? Promise.resolve()
    : new Promise(resolve => { document.addEventListener("DOMContentLoaded", resolve, {once: true}); window.addEventListener("load", resolve, {once: true}); });

  async function initialize() {
    if (location.protocol === "file:") {
      $("loading").hidden = true;
      announce("Open “Start TFSC Compliance.cmd” in the application folder to launch this workspace. The HTML file needs the local service to create Word, PDF and client folders.", true);
      return;
    }
    try {
      const config = await api("/api/config");
      const anyServices = (Array.isArray(config.catalog) && config.catalog.length) || (config.entities || []).some(e => Array.isArray(e.services) && e.services.length);
      if (!anyServices) throw new Error("The service catalog could not be loaded.");
      state.config = config; state.catalog = config.catalog || [];
      const companySelect=$("entity");companySelect.replaceChildren();
      config.entities.forEach(entity=>{const option=el("option","",entity.name);option.value=entity.name;companySelect.append(option);});
      window.setCompanyCatalog?.();
      await scriptsLoaded;
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
  // opts.entity / opts.documents: open the client for that company with those agreements ticked ("Send a BAA" from the client records)
  window.openClientDraft=async(id,opts={})=>{if(state.dirty&&!confirm("Open this client and replace unsaved changes?"))return;const d=await api(`/api/clients/${encodeURIComponent(id)}`+(opts.entity?`?entity=${encodeURIComponent(opts.entity)}`:''));if(opts.documents)d.document_types=opts.documents;applyClient(d);bypassPicker=(state.config.entities||[]).some(e=>e.name===d.entity);if(bypassPicker)picked=d.entity;window.showBuilder?.();};
  initialize();
})();
