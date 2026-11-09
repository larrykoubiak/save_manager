'use strict';

const $ = selector => document.querySelector(selector);
const h = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data = {devices:[],locations:[],cards:[],saves:[],slots:[],jobs:[],transfers:[]};
let selectedDevice = null, selectedSave = null, preview = null, transferBusy = false, managedDevice = null;
let renderedLibrary = '';
let selectedSourceSlot = null;
let deletingSave = false;
let librarySort = 'name-asc';

async function api(path, body, method='POST') {
  const options = body === undefined ? {} : {method, headers:{'Content-Type':'application/json','X-Save-Manager':'1'}, body:JSON.stringify(body)};
  const response = await fetch(`/api/${path}`, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function notice(message, error=false) {
  $('#notice').textContent = message; $('#notice').hidden = false; $('#notice').classList.toggle('error', error);
}
function badge(status, label) { return `<span class="badge ${h(status)}">${h(label || ({never:'Not scanned',online:'Available',offline:'Offline',partial:'Partly available',error:'Scan error'}[status] || status))}</span>`; }
function when(value) { return value ? new Date(value).toLocaleString([], {dateStyle:'medium',timeStyle:'short'}) : 'Never'; }
function schedule(seconds) { return seconds ? `Every ${seconds / 60} min` : 'Manual scans'; }
function deviceOf(card) { const location = data.locations.find(l => l.id === card?.location_id); return data.devices.find(d => d.id === location?.device_id); }
function locationOf(card) { return data.locations.find(l => l.id === card?.location_id); }
function currentSaves() { return data.saves.filter(s => s.present && data.cards.some(c => c.id === s.card_id && c.present)); }
function cardLabel(card) { return `${deviceOf(card)?.name || '?'} / ${locationOf(card)?.name || '?'} / ${card.filename}`; }
function canTransfer(save) {
  const card = data.cards.find(c => c.id === save.card_id);
  return save.present && card?.present && locationOf(card)?.status === 'online';
}
function canDelete(save) { return canTransfer(save) && locationOf(data.cards.find(c => c.id === save.card_id))?.writable; }
function slotsFor(save) { return (data.slots || []).filter(slot => slot.save_id === save?.id); }
function renderSlots(save) {
  const slots = slotsFor(save);
  if (!slots.length) return '';
  const occupied = slots.filter(slot => slot.state !== 'empty'), empty = slots.filter(slot => slot.state === 'empty');
  return `<div class="slot-list"><strong>Save slots · ${slots.filter(slot => slot.state === 'occupied').length} / ${slots.length}</strong>${occupied.map(slot => `<div class="slot-entry"><div>Slot ${slot.number}<small>${h(slot.label || slot.state)}${slot.error?'<br>'+h(slot.error):''}</small><code title="Matching content has the same identifier across slot numbers">${h(slot.sha256?.slice(0,8) || '')}</code></div><button data-action="slot-copy" data-id="${save.id}" data-slot="${slot.number}" ${canTransfer(save) && slot.state==='occupied'?'':'disabled'} aria-label="Copy slot ${slot.number} from ${h(data.cards.find(c=>c.id===save.card_id)?.filename)}">Copy…</button></div>`).join('') || '<small>No occupied slots</small>'}<small class="empty-slots">Empty: ${empty.map(slot => slot.number).join(', ') || 'None'}</small></div>`;
}
function switchView(view) {
  for (const name of ['devices','library','activity']) $(`#${name}-view`).hidden = name !== view;
  document.querySelectorAll('.nav').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  $('#page-title').textContent = {devices:'Devices',library:'Memory Card Browser',activity:'Activity'}[view];
  $('#page-description').textContent = {devices:'Locations, availability and scan schedules.',library:'One row per save, one column per device.',activity:'Scans, transfers and backups.'}[view];
}
function deviceHeader(device) {
  const locations = data.locations.filter(l => l.device_id === device.id);
  const busy = data.jobs.find(j => j.device_id === device.id && ['queued','running'].includes(j.status));
  return `<th scope="col" class="device-column"><strong>${h(device.name)}</strong> ${badge(busy?.status || device.status)}
    <small>${h(device.kind)} · Checked ${h(when(device.last_checked))}</small>
    <div class="table-actions"><button data-action="scan" data-id="${device.id}" ${busy || !locations.length?'disabled':''}>${busy?'Scanning…':'Scan now'}</button><button data-action="manage-device" data-id="${device.id}">Manage</button></div></th>`;
}
function noDevices() {
  return '<div class="empty">No devices configured. <button data-action="add-device">Add your first device</button></div>';
}
function render() {
  $('#device-count').textContent = data.devices.length;
  const cards = data.cards.filter(c => c.present), saves = currentSaves();
  const rows = [
    ['Type', d => h(d.kind)],
    ['Scan schedule', d => h(schedule(d.poll_seconds))],
    ['Last seen', d => h(when(d.last_seen))],
    ['Memory cards', d => cards.filter(c => deviceOf(c)?.id === d.id).length],
    ['Save copies', d => saves.filter(s => deviceOf(data.cards.find(c => c.id === s.card_id))?.id === d.id).length],
    ['Locations', d => locationList(d.id)],
  ];
  $('#devices').innerHTML = data.devices.length ? `<div class="table-wrap matrix-wrap" tabindex="0" role="region" aria-label="Device details"><table class="matrix device-table"><caption class="sr-only">Device configuration and inventory</caption><thead><tr><th scope="col">Device</th>${data.devices.map(deviceHeader).join('')}</tr></thead><tbody>${rows.map(([label,value]) => `<tr><th scope="row">${label}</th>${data.devices.map(d => `<td>${value(d)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : noDevices();
  renderLibrary(); renderActivity();
  if ($('#cards-dialog').open) renderCards();
  if ($('#manage-dialog').open) renderManage();
}
function locationList(deviceId) {
  return data.locations.filter(l => l.device_id === deviceId).map(l => `<div class="location"><div><strong>${h(l.name)}</strong> ${badge(l.status)}<div class="path">${h(l.path)} · ${h(l.suffix)} · ${l.writable?'Transfers enabled':'Read-only'}</div>${l.error?`<div class="error-text">${h(l.error)}</div>`:''}</div><button data-action="edit-location" data-id="${l.id}">Edit</button></div>`).join('') || '<span class="muted">No locations configured</span>';
}
function renderManage() {
  const device = data.devices.find(d => d.id === managedDevice);
  $('#manage-title').textContent = device.name;
  $('#manage-content').innerHTML = `<p class="hint">${h(device.kind)} · ${h(schedule(device.poll_seconds))} · Last seen ${h(when(device.last_seen))}</p><div class="table-actions"><button data-action="edit-device" data-id="${device.id}">Device settings</button><button data-action="cards" data-id="${device.id}">View cards</button><button data-action="add-location" data-id="${device.id}">Add location</button></div><h3>Locations</h3>${locationList(device.id)}`;
}
function sizeText(blocks) { return Number.isInteger(blocks) ? `${blocks.toLocaleString()} KiB` : '—'; }
function saveSizes(saves) { return saves.map(s => s.size_blocks).filter(Number.isInteger); }
function rowSize(saves) { const sizes = saveSizes(saves); return sizes.length ? Math.max(...sizes) : null; }
function sizeRange(saves) {
  const sizes = saveSizes(saves);
  if (!sizes.length) return '—';
  const min = Math.min(...sizes), max = Math.max(...sizes);
  return min === max ? sizeText(max) : `${min.toLocaleString()}–${max.toLocaleString()} KiB`;
}
function rowTitle(directory, saves) { return saves.find(s => s.row_title)?.row_title || saves.find(s => s.title)?.title || directory; }
function renderLibrary() {
  const query = $('#search').value.trim().toLowerCase(), groups = new Map();
  for (const save of currentSaves()) {
    if (!groups.has(save.directory)) groups.set(save.directory, []);
    groups.get(save.directory).push(save);
  }
  const rows = [...groups].filter(([directory,saves]) => saves.some(s => [directory,s.title,s.row_title,s.summary,cardLabel(data.cards.find(c => c.id === s.card_id))].join(' ').toLowerCase().includes(query)))
    .sort((a,b) => {
      if (librarySort.startsWith('size-')) {
        const left = rowSize(a[1]), right = rowSize(b[1]);
        if (left === null && right !== null) return 1;
        if (right === null && left !== null) return -1;
        if (left !== right) return (left - right) * (librarySort === 'size-desc' ? -1 : 1);
      }
      return rowTitle(...a).localeCompare(rowTitle(...b), undefined, {numeric:true}) * (librarySort === 'name-desc' ? -1 : 1);
    });
  $('#save-count').textContent = `${rows.length} / ${groups.size} save entries`;
  if (!data.devices.length) { $('#library').innerHTML = noDevices(); renderedLibrary = ''; return; }
  const animation = $('#animate-icons').checked ? '?v=3' : '?v=3&static=1';
  const html = `<table class="matrix comparison"><caption class="sr-only">Save copies by device</caption><thead><tr><th scope="col" aria-sort="${librarySort === 'name-asc' ? 'ascending' : librarySort === 'name-desc' ? 'descending' : 'none'}"><button class="sort-heading" data-action="sort-name" title="Sort by save name A–Z or Z–A">Save ${librarySort === 'name-asc' ? '↑' : librarySort === 'name-desc' ? '↓' : '↕'}</button></th><th scope="col" class="size-column" aria-sort="${librarySort === 'size-desc' ? 'descending' : librarySort === 'size-asc' ? 'ascending' : 'none'}"><button class="sort-heading" data-action="sort-size" title="Card space including icons and directory overhead. Ranges show different copies; sorting uses the largest copy.">Size ${librarySort === 'size-desc' ? '↓' : librarySort === 'size-asc' ? '↑' : '↕'}</button></th>${data.devices.map(deviceHeader).join('')}</tr></thead><tbody>${rows.map(([directory,saves]) => {
    const different = new Set(saves.map(s => s.sha256)).size > 1;
    const icon = saves.find(s => s.icon_hash)?.icon_hash || 'placeholder';
    return `<tr data-save="${h(directory)}"><th scope="row"><div class="save-label"><img class="save-icon" src="/api/icons/${h(icon)}.gif${animation}" alt="" width="64" height="64" loading="lazy"><div><strong>${h(rowTitle(directory, saves))}</strong><small class="directory">${h(directory)}</small>${badge(different?'partial':saves.length>1?'online':'never', different?'Different copies':saves.length>1?'Matching copies':'Single copy')}</div></div></th><td class="save-size" data-size="${rowSize(saves) ?? ''}" title="Memory-card space including icons and directory overhead; range across copies">${h(sizeRange(saves))}</td>${data.devices.map(device => {
      const copies = saves.filter(s => deviceOf(data.cards.find(c => c.id === s.card_id))?.id === device.id);
      if (!copies.length) {
        const uncertain = device.status !== 'online';
        return `<td class="missing">${uncertain?'Not in last inventory':'Not present'}${uncertain?'<small>Scan device to check</small>':''}</td>`;
      }
      return `<td>${copies.map(s => {
        const card = data.cards.find(c => c.id === s.card_id), location = locationOf(card);
        return `<div class="save-copy"><div class="copy-heading"><strong>${h(card.filename)}</strong><div class="table-actions"><button data-action="transfer" data-id="${s.id}" ${canTransfer(s)?'':'disabled'}>${slotsFor(s).length?'Whole save…':'Transfer…'}</button><button class="danger" data-action="delete-save" data-id="${s.id}" ${canDelete(s)?'':'disabled'}>Delete…</button></div></div><small>${h(location.name)}${location.status !== 'online'?' · '+h(location.status):''} · ${h(sizeText(s.size_blocks))}</small><div class="copy-progress"><small>${h(s.title || s.directory)}</small>${s.summary?`<strong>${h(s.summary)}</strong>`:''}</div><div class="copy-date">${h(s.updated || 'Unknown card date')}</div>${renderSlots(s)}<details class="copy-details"><summary>Details</summary><code>${h(s.sha256.slice(0,12))}</code><small>Last observed ${h(when(s.last_seen))}<br>${s.version_count} content version${s.version_count===1?'':'s'} observed</small></details></div>`;
      }).join('')}</td>`;
    }).join('')}</tr>`;
  }).join('') || `<tr><td colspan="${data.devices.length+2}" class="empty">${groups.size?'No matching saves.':'No saves discovered yet. Scan a device to populate the table.'}</td></tr>`}</tbody></table>`;
  if (html === renderedLibrary) return;
  renderedLibrary = html;
  // Preserve open details, icons and scroll position during background refreshes.
  const iconKey = img => img.closest('tr').dataset.save + '|' + img.getAttribute('src');
  const icons = new Map([...$('#library').querySelectorAll('img')].map(img => [iconKey(img), img]));
  const previous = $('#library .matrix-wrap');
  const scroll = [previous?.scrollLeft || 0,previous?.scrollTop || 0];
  const open = [...$('#library').querySelectorAll('details[open]')].map(d => d.closest('tr').dataset.save+'|'+d.closest('.save-copy').querySelector('[data-id]').dataset.id);
  $('#library').innerHTML = `<div class="table-wrap matrix-wrap" tabindex="0" role="region" aria-label="Save comparison">${html}</div>`;
  const wrapper = $('#library .matrix-wrap'); wrapper.scrollLeft = scroll[0]; wrapper.scrollTop = scroll[1];
  wrapper.querySelectorAll('img').forEach(img => { const old = icons.get(iconKey(img)); if (old) img.replaceWith(old); });
  wrapper.querySelectorAll('details').forEach(d => { d.open = open.includes(d.closest('tr').dataset.save+'|'+d.closest('.save-copy').querySelector('[data-id]').dataset.id); });
}
function renderCards() {
  const device = data.devices.find(d => d.id === selectedDevice);
  $('#cards-title').textContent = `${device?.name || 'Device'} · memory cards`;
  const locationIds = new Set(data.locations.filter(l => l.device_id === selectedDevice).map(l => l.id));
  const cards = data.cards.filter(c => locationIds.has(c.location_id));
  $('#cards-content').innerHTML = cards.map(c => {
    const location = locationOf(c), saves = data.saves.filter(s => s.card_id === c.id && s.present);
    return `<article><div class="save-heading"><h3>${h(c.filename)}</h3>${badge(c.present?location.status:'offline',c.present?undefined:'Missing at last scan')}</div><p class="hint">${h(location.name)} · ${c.formatted?`${c.free_blocks.toLocaleString()} / ${c.total_blocks.toLocaleString()} KiB free`:'Unformatted card'} · Last seen ${h(when(c.last_seen))}</p>${c.formatted?`<progress max="${c.total_blocks}" value="${c.total_blocks-c.free_blocks}" aria-label="Card capacity used"></progress>`:''}
      <div class="table-wrap"><table><thead><tr><th>Save</th><th>Directory</th><th></th></tr></thead><tbody>${saves.map(s => `<tr><td>${h(s.title || s.directory)}</td><td>${h(s.directory)}</td><td><div class="table-actions"><button data-action="transfer" data-id="${s.id}" ${canTransfer(s)?'':'disabled'}>Transfer…</button><button class="danger" data-action="delete-save" data-id="${s.id}" ${canDelete(s)?'':'disabled'}>Delete…</button></div></td></tr>`).join('') || '<tr><td colspan="3">No saves in the last known inventory.</td></tr>'}</tbody></table></div></article>`;
  }).join('') || '<div class="empty">No cards discovered yet. Add a location and scan this device.</div>';
}
function renderActivity() {
  const events = [...data.jobs.map(j => ({...j, type:'scan', label:`${j.reason === 'poll'?'Scheduled':'Device'} scan`, detail:data.devices.find(d => d.id === j.device_id)?.name})),
    ...data.transfers.map(t => ({...t,type:'transfer',label:t.action,detail:`${t.directory}${t.source_slot?' · slot '+t.source_slot+' → '+t.destination_slot:''} → ${data.cards.find(c => c.id === t.destination_card_id)?.filename || '?'}`}))].sort((a,b) => b.created.localeCompare(a.created));
  $('#activity').innerHTML = events.length ? `<div class="table-wrap"><table><thead><tr><th>Operation</th><th>Status</th><th>Started</th><th>Details</th></tr></thead><tbody>${events.map(e => `<tr><td>${h(e.label)}<small>${h(e.detail)}</small></td><td>${badge(e.status)}</td><td>${h(when(e.created))}</td><td>${e.error?`<span class="error-text">${h(e.error)}</span>`:''}${e.backup?`Backup: <code>${h(e.backup)}</code>`:''}${!e.error && !e.backup ? h(e.finished?`Finished ${when(e.finished)}`:'Awaiting completion'):''}${e.status==='preview'?`<button data-action="cancel-preview" data-id="${h(e.id)}">Cancel preview</button>`:''}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Your scans and transfers will appear here.</div>';
}
async function refresh() { data = await api('inventory'); render(); }
function openDevice(id) {
  const form = $('#device-form'); form.reset(); form.dataset.id = id || ''; form.querySelector('.form-error').textContent = '';
  const device = data.devices.find(d => d.id === id);
  if (device) for (const field of ['name','kind','poll_seconds']) {
    const input = form.elements[field];
    if (input.tagName === 'SELECT' && ![...input.options].some(o => o.value === String(device[field]))) input.add(new Option(String(device[field]),String(device[field])));
    input.value = device[field];
  }
  $('#device-form-title').textContent = device?'Device settings':'Add a device'; $('#device-dialog').showModal();
}
function openLocation(deviceId, id) {
  const form = $('#location-form'); form.reset(); form.dataset.deviceId = deviceId; form.dataset.id = id || ''; form.querySelector('.form-error').textContent = '';
  const location = data.locations.find(l => l.id === id);
  for (const name of ['path','suffix']) form.elements[name].disabled = Boolean(location);
  if (location) {
    for (const name of ['name','path','suffix','marker']) form.elements[name].value = location[name];
    form.elements.writable.checked = Boolean(location.writable);
  }
  $('#location-form-title').textContent = location?'Location settings':'Add a location'; $('#location-dialog').showModal();
}
function updateDestinationSlots() {
  if (selectedSourceSlot === null) return;
  const form = $('#transfer-form'), cardId = Number(form.elements.destination_card_id.value);
  const targetSave = data.saves.find(s => s.card_id === cardId && s.directory === selectedSave.directory && s.present);
  const slots = slotsFor(targetSave), select = form.elements.destination_slot;
  select.innerHTML = '<option value="">Choose a slot…</option>' + slots.map(slot => {
    const same = cardId === selectedSave.card_id && slot.number === selectedSourceSlot;
    return `<option value="${slot.number}" ${same || slot.state==='invalid'?'disabled':''}>Slot ${slot.number} · ${h(slot.state)}${slot.label?' · '+h(slot.label):''}${same?' (source)':''}</option>`;
  }).join('');
  select.disabled = !slots.length;
  const empty = slots.find(slot => slot.state === 'empty');
  if (empty) select.value = empty.number;
}
function openTransfer(id, slotNumber=null, deleting=false) {
  deletingSave = deleting;
  selectedSourceSlot = slotNumber;
  selectedSave = data.saves.find(s => s.id === id); preview = null;
  const card = data.cards.find(c => c.id === selectedSave.card_id), form = $('#transfer-form');
  const isSlot = selectedSourceSlot !== null;
  $('#transfer-form .dialog-heading h2').textContent = deletingSave ? 'Delete a save from this card' : isSlot ? 'Copy a save slot' : 'Transfer a save';
  $('#idle-text').textContent = deletingSave ? 'Games and emulators using this card are closed on every device.' : 'Games and emulators using both cards are closed on every device.';
  $('#conflict-label').textContent = isSlot ? 'If the destination slot is occupied' : 'If a different save occupies the same directory';
  form.reset(); form.querySelector('.form-error').textContent = '';
  $('#transfer-source').innerHTML = `<strong>${h(selectedSave.title || selectedSave.directory)}${isSlot?' · source slot '+selectedSourceSlot:''}</strong><br>${h(cardLabel(card))}<br><code>${h(selectedSave.directory)}</code>${deletingSave?'<p class="error-text">This removes the entire save from this card, including every slot inside it. Copies on other cards are kept.</p>':''}`;
  const destinations = data.cards.filter(c => {
    if (!c.present || !c.formatted || (!isSlot && c.id === card.id) || !locationOf(c)?.writable || locationOf(c)?.status !== 'online') return false;
    if (!isSlot) return true;
    const save = data.saves.find(s => s.card_id === c.id && s.directory === selectedSave.directory && s.present);
    const slots = slotsFor(save);
    return slots.length > 0 && slots.every(slot => slot.state !== 'invalid');
  });
  $('#slot-controls').hidden = !isSlot;
  form.elements.destination_slot.required = isSlot;
  form.elements.destination_slot.disabled = !isSlot;
  form.elements.conflict.innerHTML = isSlot
    ? '<option value="stop">Stop if the destination slot is occupied</option><option value="replace">Replace the selected destination slot</option>'
    : '<option value="stop">Stop and ask me</option><option value="keep_both">Keep both (supported games)</option><option value="replace">Replace destination save</option>';
  form.elements.destination_card_id.innerHTML = '<option value="">Choose a card…</option>' + destinations.map(c => `<option value="${c.id}">${h(cardLabel(c))} · ${c.free_blocks} KiB free</option>`).join('');
  form.elements.destination_card_id.required = !deletingSave;
  form.elements.destination_card_id.disabled = deletingSave;
  if (isSlot) updateDestinationSlots();
  $('#transfer-options').hidden = deletingSave; $('#transfer-preview').hidden = true; $('#idle-label').hidden = true;
  $('#transfer-submit').textContent = deletingSave ? 'Preview deletion' : 'Preview transfer';
  $('#transfer-submit').classList.toggle('danger', deletingSave);
  $('#transfer-submit').disabled = deletingSave ? !canDelete(selectedSave) : !destinations.length;
  if (!deletingSave && !destinations.length) form.querySelector('.form-error').textContent = isSlot
    ? 'No writable destination with a valid save for this game is available. Create a save in-game first, enable transfers, and scan its device.'
    : 'No destination is available. Add another location, enable reviewed transfers, and scan it successfully.';
  $('#transfer-dialog').showModal();
}
async function formSubmit(form, work) {
  const button = form.querySelector('button[type=submit]'); button.disabled = true;
  form.querySelector('.form-error').textContent = '';
  try { await work(); } catch (error) { form.querySelector('.form-error').textContent = error.message; }
  finally { button.disabled = false; }
}
$('#device-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  formSubmit(form, async () => {
    const id = form.dataset.id, body = {name:form.elements.name.value,kind:form.elements.kind.value,poll_seconds:Number(form.elements.poll_seconds.value)};
    const device = await api(id?`devices/${id}`:'devices',body,id?'PUT':'POST');
    $('#device-dialog').close(); await refresh();
    if (!id) openLocation(device.id);
  });
});
$('#location-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  formSubmit(form, async () => {
    const id = form.dataset.id, deviceId = form.dataset.deviceId;
    const body = Object.fromEntries(['name','path','suffix','marker'].map(name => [name,form.elements[name].value]));
    body.writable = form.elements.writable.checked;
    await api(`devices/${deviceId}/locations${id?`/${id}`:''}`,body,id?'PUT':'POST');
    $('#location-dialog').close(); await refresh(); notice('Location saved. Scan the device to discover its cards.');
  });
});
$('#transfer-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  formSubmit(form, async () => {
    transferBusy = true;
    try {
      if (!preview) {
        const body = deletingSave ? {save_id:selectedSave.id,action:'delete'} : {save_id:selectedSave.id,destination_card_id:Number(form.elements.destination_card_id.value),conflict:form.elements.conflict.value};
        if (selectedSourceSlot !== null) Object.assign(body, {source_slot:selectedSourceSlot,destination_slot:Number(form.elements.destination_slot.value)});
        const result = await api('transfers/preview',body);
        preview = result;
        $('#transfer-options').hidden = true; $('#transfer-preview').hidden = false; $('#idle-label').hidden = false;
        $('#transfer-preview').innerHTML = `<p><strong>${h(result.action)}</strong><br>${deletingSave?'Card: '+h(result.destination):'From: '+h(result.source)+'<br>To: '+h(result.destination)}<br>${deletingSave?'Remove':'Destination directory'}: <code>${h(result.destination_save)}</code></p>${result.warnings.map(w => `<p class="error-text">${h(w)}</p>`).join('')}<p class="hint">Preview expires in 10 minutes. A full card backup will be saved before writing. ${deletingSave?'The card':'Both cards'} will be checked again before writing.</p>`;
        $('#transfer-submit').textContent = deletingSave ? 'Confirm deletion' : 'Confirm transfer';
      } else {
        if (!form.elements.cards_idle.checked) throw new Error(deletingSave ? 'Confirm that games and emulators using this card are closed.' : 'Confirm that games and emulators using both cards are closed.');
        const key = preview.id;
        try {
          const result = await api(`transfers/${key}/apply`,{cards_idle:true});
          preview = null; $('#transfer-dialog').close();
          notice(result.backup?`${deletingSave?'Save deleted':'Transfer complete'}. Backup: ${result.backup}`:'The save is already identical. No write was needed.');
        } catch (error) {
          preview = null; $('#transfer-options').hidden = deletingSave; $('#transfer-preview').hidden = true; $('#idle-label').hidden = true;
          form.elements.cards_idle.checked = false;
          $('#transfer-submit').textContent = deletingSave ? 'Preview deletion' : 'Preview transfer'; throw error;
        }
      }
      await refresh();
    } finally { transferBusy = false; }
  });
});
$('#transfer-dialog').addEventListener('cancel', event => { if (transferBusy) event.preventDefault(); });
$('#transfer-dialog').addEventListener('close', () => {
  if (preview) { const key = preview.id; preview = null; api(`transfers/${key}/cancel`,{}).then(refresh).catch(error => notice(error.message,true)); }
});
document.addEventListener('click', async event => {
  const close = event.target.closest('[data-close]');
  if (close) { if (!transferBusy) close.closest('dialog').close(); return; }
  const nav = event.target.closest('[data-view]'); if (nav) { switchView(nav.dataset.view); return; }
  const button = event.target.closest('[data-action]'); if (!button) return;
  const id = Number(button.dataset.id);
  try {
    switch (button.dataset.action) {
      case 'add-device': openDevice(); break;
      case 'edit-device': openDevice(id); break;
      case 'manage-device': managedDevice = id; renderManage(); $('#manage-dialog').showModal(); break;
      case 'add-location': openLocation(id); break;
      case 'edit-location': { const l = data.locations.find(l => l.id === id); openLocation(l.device_id,id); break; }
      case 'scan': button.disabled = true; await api(`devices/${id}/scan`,{}); await refresh(); break;
      case 'cards': selectedDevice = id; renderCards(); $('#cards-dialog').showModal(); break;
      case 'transfer': openTransfer(id); break;
      case 'delete-save': openTransfer(id, null, true); break;
      case 'sort-size': librarySort = librarySort === 'size-desc' ? 'size-asc' : 'size-desc'; renderLibrary(); break;
      case 'sort-name': librarySort = librarySort === 'name-asc' ? 'name-desc' : 'name-asc'; renderLibrary(); break;
      case 'slot-copy': openTransfer(id, Number(button.dataset.slot)); break;
      case 'cancel-preview': await api(`transfers/${button.dataset.id}/cancel`,{}); await refresh(); break;
    }
  } catch (error) { notice(error.message,true); button.disabled = false; }
});
$('#add-device').addEventListener('click', () => openDevice());
$('#transfer-form').elements.destination_card_id.addEventListener('change', updateDestinationSlots);
$('#search').addEventListener('input', renderLibrary);
$('#animate-icons').checked = !matchMedia('(prefers-reduced-motion: reduce)').matches;
$('#animate-icons').addEventListener('change', renderLibrary);
refresh().catch(error => notice(`Could not load the library: ${error.message}`,true));
setInterval(() => { if (!document.hidden) refresh().catch(error => notice(`Connection interrupted: ${error.message}`,true)); }, 4000);
