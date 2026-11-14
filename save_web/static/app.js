'use strict';

const $ = selector => document.querySelector(selector);
const h = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data = {devices:[],locations:[],cards:[],saves:[],slots:[],jobs:[],transfers:[],sync_groups:[],sync_members:[]};
let selectedDevice = null, selectedSave = null, preview = null, transferBusy = false, managedDevice = null;
let renderedLibrary = '';
let selectedSourceSlot = null;
let deletingSave = false;
let librarySort = 'name-asc';
let activeSystem = 'ps2', activeView = 'saves';
let renderedSavedLibrary = '';
const systemState = new Map();

async function api(path, body, method='POST') {
  const options = body === undefined ? {} : {method, headers:{'Content-Type':'application/json','X-Save-Manager':'1'}, body:JSON.stringify(body)};
  const response = await fetch(`/api/${path}`, options);
  const mime = (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
  if (mime !== 'application/json' && !mime.endsWith('+json')) {
    if (response.redirected) throw new Error('A sign-in page was returned. Reload and sign in again.');
    throw new Error(response.ok ? 'The server returned a page instead of app data. Reload to reconnect.' : `Server unavailable (HTTP ${response.status}).`);
  }
  let result;
  try { result = await response.json(); }
  catch { throw new Error('The server returned an incomplete response. Try again.'); }
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function notice(message, error=false, kind='action') {
  $('#notice').textContent = message; $('#notice').hidden = false; $('#notice').classList.toggle('error', error);
  $('#notice').dataset.kind = kind;
}
function badge(status, label) { return `<span class="badge ${h(status)}">${h(label || ({never:'Not scanned',online:'Available',offline:'Offline',partial:'Partly available',error:'Scan error'}[status] || status))}</span>`; }
function when(value) { return value ? new Date(value).toLocaleString([], {dateStyle:'medium',timeStyle:'short'}) : 'Never'; }
function schedule(seconds) { return seconds ? `Every ${seconds / 60} min` : 'Manual scans'; }
function deviceOf(card) { const location = data.locations.find(l => l.id === card?.location_id); return data.devices.find(d => d.id === location?.device_id); }
function locationOf(card) { return data.locations.find(l => l.id === card?.location_id); }
function backendOf(card) { return locationOf(card)?.backend || 'ps2'; }
function saveBackend(save) { return backendOf(data.cards.find(c => c.id === save.card_id)); }
function currentSaves() { return data.saves.filter(s => s.present && data.cards.some(c => c.id === s.card_id && c.present)); }
function cardLabel(card) { return `${deviceOf(card)?.name || '?'} / ${locationOf(card)?.name || '?'} / ${card.filename}`; }
function canTransfer(save) {
  const card = data.cards.find(c => c.id === save.card_id);
  return save.present && card?.present && locationOf(card)?.status === 'online';
}
function canWriteCard(card) { return locationOf(card)?.writable && !card.read_only; }
function canDelete(save) { return canTransfer(save) && canWriteCard(data.cards.find(c => c.id === save.card_id)); }
function slotsFor(save) { return (data.slots || []).filter(slot => slot.save_id === save?.id); }
function renderSlots(save) {
  if (save.card_slots) {
    const slot = save.card_slots;
    return `<div class="slot-list"><div class="slot-entry"><div>Slot ${slot.number}<small>One block per save</small></div><button data-action="slot-copy" data-id="${save.id}" data-slot="${slot.number}" ${canTransfer(save) && slot.valid?'':'disabled'}>Copy to slot…</button></div></div>`;
  }
  const slots = slotsFor(save);
  if (!slots.length) return '';
  const occupied = slots.filter(slot => slot.state !== 'empty'), empty = slots.filter(slot => slot.state === 'empty');
  return `<div class="slot-list"><strong>Save slots · ${slots.filter(slot => slot.state === 'occupied').length} / ${slots.length}</strong>${occupied.map(slot => `<div class="slot-entry"><div>Slot ${slot.number}<small>${h(slot.label || slot.state)}${slot.error?'<br>'+h(slot.error):''}</small><code title="Matching content has the same identifier across slot numbers">${h(slot.sha256?.slice(0,8) || '')}</code></div><button data-action="slot-copy" data-id="${save.id}" data-slot="${slot.number}" ${canTransfer(save) && slot.state==='occupied'?'':'disabled'} aria-label="Copy slot ${slot.number} from ${h(data.cards.find(c=>c.id===save.card_id)?.filename)}">Copy…</button></div>`).join('') || '<small>No occupied slots</small>'}<small class="empty-slots">Empty: ${empty.map(slot => slot.number).join(', ') || 'None'}</small></div>`;
}
function systems() { return data.backends?.length ? data.backends : [{id:'ps2',name:'PS2'},{id:'ps1',name:'PS1'}]; }
function systemName(system=activeSystem) { return systems().find(s => s.id === system)?.name || system.toUpperCase(); }
function scopedView() { return ['saves','library','sync'].includes(activeView); }
function systemLocations(system=activeSystem) { return data.locations.filter(l => l.backend === system); }
function systemDevices() { const ids = new Set(systemLocations().map(l => l.device_id)); return data.devices.filter(d => ids.has(d.id)); }
function applyRoute() {
  const match = location.hash.match(/^#\/systems\/([^/]+)\/(saves|library|sync)$/);
  let system = activeSystem, view = 'saves';
  if (match && systems().some(s => s.id === match[1])) { system = match[1]; view = match[2]; }
  else if (['#/devices','#/activity'].includes(location.hash)) view = location.hash.slice(2);
  else history.replaceState(null,'',`#/systems/${activeSystem}/saves`);
  if (system !== activeSystem) {
    systemState.set(activeSystem,{search:$('#search').value,sort:librarySort});
    const state = systemState.get(system);
    $('#search').value = state?.search || ''; librarySort = state?.sort || 'name-asc';
  }
  activeSystem = system; activeView = view;
}
function switchView(view,system=activeSystem) {
  const hash = ['saves','library','sync'].includes(view) ? `#/systems/${system}/${view}` : `#/${view}`;
  if (location.hash !== hash) history.pushState(null,'',hash);
  applyRoute(); render();
}
function renderNavigation() {
  for (const name of ['devices','saves','library','activity','sync']) $(`#${name}-view`).hidden = name !== activeView;
  document.querySelectorAll('.nav').forEach(button => button.classList.toggle('active', button.dataset.view === activeView));
  const name = systemName();
  $('#page-title').textContent = {devices:'Devices',saves:`${name} Save Library`,library:`${name} Memory Card Browser`,activity:'Activity',sync:`${name} Automatic Sync`}[activeView];
  $('#page-description').textContent = {saves:`Your ${name} games and save slots, stored independently of memory cards.`,devices:'Shared devices, their systems, locations and scan schedules.',library:`${name} saves and sources. One column per device.`,activity:'Scans, transfers and backups across all systems.',sync:`Shared ${name} memory cards, queued updates and conflicts.`}[activeView];
  $('#system-nav').innerHTML = systems().map(system => {
    const count = activeView === 'saves' ? (data.library?.games || []).filter(game => game.backend === system.id).length : new Set(currentSaves().filter(s => saveBackend(s) === system.id).map(s => s.directory)).size;
    const selected = scopedView() && activeSystem === system.id;
    return `<a href="#/systems/${h(system.id)}/${['saves','sync'].includes(activeView)?activeView:'library'}" data-system="${h(system.id)}" class="system-tab ${selected?'active':''}" ${selected?'aria-current="page"':''}>${h(system.name)} <span class="count">${count}</span></a>`;
  }).join('');
}
function playtime(seconds) {
  if (seconds === null || seconds === undefined) return 'Unknown';
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds / 60) % 60;
  return `${hours}:${String(minutes).padStart(2,'0')}:${String(seconds % 60).padStart(2,'0')}`;
}
function cardPortLabel(ports) { return {port1:'Port 1 only',both:'Both ports',unknown:'Unknown'}[ports] || 'Unknown'; }
function openGameSettings(id) {
  const game = data.library.games.find(game => game.id === id);
  if (!game) throw new Error('Game not found. Refresh the library.');
  const form = $('#game-settings-form');
  form.dataset.id = id; form.querySelector('.form-error').textContent = '';
  form.elements.card_ports.value = game.card_ports || 'unknown';
  form.elements.card_port_note.value = game.card_port_note || '';
  $('#game-settings-title').textContent = game.serial ? `${game.title} · ${game.serial}` : game.title;
  $('#game-settings-dialog').showModal();
}
function renderSavedLibrary() {
  const library = data.library || {games:[],errors:[],previous_versions:2};
  const errors = library.errors.filter(error => error.backend === activeSystem);
  const search = $('#saved-search').value.trim().toLowerCase();
  const games = library.games.filter(game => game.backend === activeSystem && (!search || JSON.stringify(game).toLowerCase().includes(search)));
  const count = games.reduce((total,game) => total + game.saves.length,0);
  $('#saved-count').textContent = `${games.length} game${games.length === 1?'':'s'} · ${count} save${count === 1?'':'s'}`;
  const input = $('#library-settings-form').elements.previous_versions;
  if (document.activeElement !== input) input.value = library.previous_versions;
  const signature = JSON.stringify([activeSystem,games,errors,search]);
  if (signature === renderedSavedLibrary) return;
  const expanded = new Set([...$('#saved-library').querySelectorAll('details[open][data-library-key]')].map(node => node.dataset.libraryKey));
  $('#saved-library').innerHTML = games.map(game => `<details class="saved-game" data-library-key="game-${game.id}" ${expanded.has(`game-${game.id}`)?'open':''}>
    <summary>${game.icon_hash?`<img class="saved-icon" src="/api/icons/${h(game.icon_hash)}.gif" alt="" loading="lazy">`:''}<strong>${h(game.title)}</strong>${game.serial?`<code class="game-serial">${h(game.serial)}</code>`:''}<span class="hint">${game.saves.length} save${game.saves.length === 1?'':'s'}</span></summary>
    <div class="game-card-ports"><span>Memory-card ports: <strong>${h(cardPortLabel(game.card_ports))}</strong></span> <button data-action="game-settings" data-id="${game.id}">Game settings</button>${game.card_port_note?`<p class="game-port-note">${h(game.card_port_note)}</p>`:''}</div>
    <div class="table-wrap"><table class="matrix saved-table"><thead><tr><th>Save</th><th>Playtime</th><th>Sources</th><th>Actions</th></tr></thead><tbody>${game.saves.map(save => {
      const sources = save.sources;
      const history = save.revisions.filter(version => version.id !== save.id);
      const historyKey = `save-${game.id}-${save.save_key}`;
      const historyHtml = history.length ? `<details data-library-key="${h(historyKey)}" ${expanded.has(historyKey)?'open':''}><summary>Revision history (${history.length})</summary>${history.map(version => `<div class="save-revision">${h(version.label)}<small>${h(when(version.created))} · ${h(version.device)} · <a href="/api/library/versions/${version.id}/download" download>Download revision</a></small></div>`).join('')}</details>` : '';
      return `<tr data-library-save="${h(save.content_hash)}" data-library-version="${save.id}"><td>${h(save.label)}<small><code>${h(save.content_hash.slice(0,8))}</code> · Imported ${h(when(save.created))}</small>${historyHtml}</td><td>${h(playtime(save.playtime_seconds))}</td>
      <td>${sources.length?sources.map(source => `<div>${h(source.device)}<small>${h(source.filename)}${source.original_slot === null?'':` · Original slot ${source.original_slot}`} · ${source.deleted?'Removed source':!source.present?'Save absent':source.available?'Available':'Offline'}</small></div>`).join(''):`<div>${h(save.device)}<small>${h(save.filename)} · Retained copy${save.original_slots.length?` · Original slot ${h(save.original_slots.join(', '))}`:''}</small></div>`}</td>
      <td><a href="/api/library/versions/${save.id}/download" download>Download</a></td></tr>`;
    }).join('')}</tbody></table></div></details>`).join('') || `<div class="empty">No ${h(systemName())} saves stored yet. Scan a device from Cards or Devices to collect its saves.</div>`;
  $('#library-errors').innerHTML = errors.length ? `<details class="library-errors"><summary>${errors.length} save import issue${errors.length === 1?'':'s'}</summary>${errors.map(error => `<p>${h(error.device)} · ${h(error.filename)} · ${h(error.directory)}<br><span class="error-text">${h(error.error)}</span></p>`).join('')}</details>` : '';
  renderedSavedLibrary = signature;
}
function locationStatus(locations) {
  if (!locations.length) return 'never';
  if (locations.every(l => l.status === 'online')) return 'online';
  if (locations.some(l => l.status === 'online')) return 'partial';
  if (locations.some(l => l.status === 'error')) return 'error';
  return locations.some(l => l.status === 'offline') ? 'offline' : 'never';
}
function deviceHeader(device,system=null) {
  const locations = data.locations.filter(l => l.device_id === device.id && (!system || l.backend === system));
  const busy = data.jobs.find(j => j.device_id === device.id && ['queued','running'].includes(j.status));
  const status = system ? locationStatus(locations) : device.status;
  const checked = system ? locations.map(l => l.last_scanned).filter(Boolean).sort().at(-1) : device.last_checked;
  return `<th scope="col" class="device-column"><strong>${h(device.name)}</strong> ${badge(busy?.status || status)}
    <small>${h(device.kind)} · Checked ${h(when(checked))}</small>
    <div class="table-actions"><button data-action="scan" data-id="${device.id}" ${busy || !locations.length?'disabled':''}>${busy?'Scanning…':'Scan now'}</button><button data-action="manage-device" data-id="${device.id}">Manage</button></div></th>`;
}
function noDevices() {
  return '<div class="empty">No devices configured. <button data-action="add-device">Add your first device</button></div>';
}
function render() {
  renderNavigation(); renderSystemSources(); renderSavedLibrary();
  $('#device-count').textContent = data.devices.length;
  const cards = data.cards.filter(c => c.present), saves = currentSaves();
  const rows = [
    ['Type', d => h(d.kind)],
    ['Connection', d => d.transport === 'ftp' ? `FTP · ${h(d.ftp_host)}:${d.ftp_port}` : 'Mounted folder'],
    ['Root folder', d => `<code>${h(d.root_path)}</code>`],
    ['Scan schedule', d => h(schedule(d.poll_seconds))],
    ['Last seen', d => h(when(d.last_seen))],
    ['Memory cards', d => cards.filter(c => deviceOf(c)?.id === d.id).length],
    ['Save copies', d => saves.filter(s => deviceOf(data.cards.find(c => c.id === s.card_id))?.id === d.id).length],
    ['Locations', d => locationList(d.id)],
  ];
  $('#devices').innerHTML = data.devices.length ? `<div class="table-wrap matrix-wrap" tabindex="0" role="region" aria-label="Device details"><table class="matrix device-table"><caption class="sr-only">Device configuration and inventory</caption><thead><tr><th scope="col">Device</th>${data.devices.map(d => deviceHeader(d)).join('')}</tr></thead><tbody>${rows.map(([label,value]) => `<tr><th scope="row">${label}</th>${data.devices.map(d => `<td>${value(d)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : noDevices();
  renderLibrary(); renderActivity(); renderSync();
  if ($('#cards-dialog').open) renderCards();
  if ($('#manage-dialog').open) renderManage();
}
function renderSystemSources() {
  const locations = systemLocations();
  $('#source-device-hint').textContent = data.devices.length ? `Add a location to connect another ${systemName()} folder.` : 'Add a device first, then connect a folder for this system.';
  $('#system-sources-title').textContent = `${systemName()} sources · ${locations.length} location${locations.length===1?'':'s'}`;
  $('#system-source-list').innerHTML = locations.length ? `<div class="table-wrap"><table><thead><tr><th>Device</th><th>Location</th><th>Folder</th><th>Status</th><th></th></tr></thead><tbody>${locations.map(l => {
    const device = data.devices.find(d => d.id === l.device_id);
    return `<tr><td>${h(device?.name)}</td><td>${h(l.name)}<small>${l.writable?'Writes enabled':'Read-only'}</small></td><td><code>${h(l.path)}</code><small>${h(l.suffix === '*'?'All supported files':l.suffix)}</small></td><td>${badge(l.status)}${l.error?`<small class="error-text">${h(l.error)}</small>`:''}</td><td><div class="table-actions">${locationActions(l)}</div></td></tr>`;
  }).join('')}</tbody></table></div>` : `<p class="hint">No ${h(systemName())} locations configured.</p>`;
  $('#add-system-location').textContent = `Add ${systemName()} location…`;
  $('#add-system-location').disabled = !data.devices.length;
}
function locationActions(location) {
  return `<button data-action="edit-location" data-id="${location.id}">Edit</button><button class="danger" data-action="delete-location" data-id="${location.id}">Remove…</button>`;
}
function openDeleteLocation(id) {
  const location = data.locations.find(l => l.id === id);
  if (!location) throw new Error('Location no longer exists.');
  if (scopedView() && location.backend !== activeSystem) throw new Error('Open this location’s system page to remove it.');
  const device = data.devices.find(d => d.id === location.device_id);
  const form = $('#delete-location-form');
  form.dataset.id = id; form.dataset.deviceId = location.device_id;
  form.querySelector('.form-error').textContent = '';
  $('#delete-location-summary').textContent = `${device.name} / ${location.name} · ${location.path}`;
  $('#delete-location-dialog').showModal();
}
function locationList(deviceId,system=null) {
  return data.locations.filter(l => l.device_id === deviceId && (!system || l.backend === system)).map(l => `<div class="location"><div><strong>${h(l.name)}</strong> ${badge(l.status)}<div class="path"><a href="#/systems/${h(l.backend)}/library" data-system="${h(l.backend)}">${h(systemName(l.backend))}</a> · ${h(l.path)} · ${h(l.suffix)} · ${l.writable?'Writes enabled':'Read-only'}</div>${l.error?`<div class="error-text">${h(l.error)}</div>`:''}</div><div class="table-actions">${locationActions(l)}</div></div>`).join('') || '<span class="muted">No locations configured</span>';
}
function renderManage() {
  const device = data.devices.find(d => d.id === managedDevice);
  $('#manage-title').textContent = device.name;
  $('#manage-content').innerHTML = `<p class="hint">${h(device.kind)} · ${h(schedule(device.poll_seconds))} · Last seen ${h(when(device.last_seen))}</p><div class="table-actions"><button data-action="edit-device" data-id="${device.id}">Device settings</button><button class="danger" data-action="delete-device" data-id="${device.id}">Remove device…</button><button data-action="cards" data-id="${device.id}">View cards</button><button data-action="add-location" data-id="${device.id}">Add location</button></div><h3>Locations</h3>${locationList(device.id,scopedView()?activeSystem:null)}`;
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
  const devices = systemDevices();
  const query = $('#search').value.trim().toLowerCase(), groups = new Map();
  for (const save of currentSaves().filter(s => saveBackend(s) === activeSystem)) {
    const key = JSON.stringify([saveBackend(save),save.directory]);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(save);
  }
  const rows = [...groups.values()].map(saves => [saves[0].directory,saves]).filter(([directory,saves]) => saves.some(s => [directory,saveBackend(s),s.title,s.row_title,s.summary,cardLabel(data.cards.find(c => c.id === s.card_id))].join(' ').toLowerCase().includes(query)))
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
  if (!devices.length) {
    $('#library').innerHTML = `<p class="empty">Add a ${h(systemName())} location above, then scan its device to discover saves.</p>`;
    renderedLibrary = ''; return;
  }
  const animation = $('#animate-icons').checked ? '?v=4' : '?v=4&static=1';
  const html = `<table class="matrix comparison"><caption class="sr-only">Save copies by device</caption><thead><tr><th scope="col" aria-sort="${librarySort === 'name-asc' ? 'ascending' : librarySort === 'name-desc' ? 'descending' : 'none'}"><button class="sort-heading" data-action="sort-name" title="Sort by save name A–Z or Z–A">Save ${librarySort === 'name-asc' ? '↑' : librarySort === 'name-desc' ? '↓' : '↕'}</button></th><th scope="col" class="size-column" aria-sort="${librarySort === 'size-desc' ? 'descending' : librarySort === 'size-asc' ? 'ascending' : 'none'}"><button class="sort-heading" data-action="sort-size" title="Card space including icons and directory overhead. Ranges show different copies; sorting uses the largest copy.">Size ${librarySort === 'size-desc' ? '↓' : librarySort === 'size-asc' ? '↑' : '↕'}</button></th>${devices.map(d => deviceHeader(d,activeSystem)).join('')}</tr></thead><tbody>${rows.map(([directory,saves]) => {
    const different = new Set(saves.map(s => s.sha256)).size > 1;
    const icon = saves.find(s => s.icon_hash)?.icon_hash || 'placeholder';
    return `<tr data-save="${h(directory)}" data-backend="${saveBackend(saves[0])}"><th scope="row"><div class="save-label"><img class="save-icon" src="/api/icons/${h(icon)}.gif${animation}" alt="" width="64" height="64" loading="lazy"><div><strong>${h(rowTitle(directory, saves))}</strong><small class="directory">${saveBackend(saves[0]).toUpperCase()} · ${h(directory)}</small>${badge(different?'partial':saves.length>1?'online':'never', different?'Different copies':saves.length>1?'Matching copies':'Single copy')}</div></div></th><td class="save-size" data-size="${rowSize(saves) ?? ''}" title="Memory-card space including icons and directory overhead; range across copies">${h(sizeRange(saves))}</td>${devices.map(device => {
      const copies = saves.filter(s => deviceOf(data.cards.find(c => c.id === s.card_id))?.id === device.id);
      if (!copies.length) {
        const uncertain = locationStatus(systemLocations().filter(l => l.device_id === device.id)) !== 'online';
        return `<td class="missing">${uncertain?'Not in last inventory':'Not present'}${uncertain?'<small>Scan device to check</small>':''}</td>`;
      }
      return `<td>${copies.map(s => {
        const card = data.cards.find(c => c.id === s.card_id), location = locationOf(card);
        return `<div class="save-copy"><div class="copy-heading"><strong>${h(card.filename)}</strong><div class="table-actions"><button data-action="transfer" data-id="${s.id}" ${canTransfer(s)?'':'disabled'}>${slotsFor(s).length?'Whole save…':'Transfer…'}</button><button class="danger" data-action="delete-save" data-id="${s.id}" ${canDelete(s)?'':'disabled'}>Delete…</button></div></div><small>${h(location.name)}${location.status !== 'online'?' · '+h(location.status):''} · ${h(sizeText(s.size_blocks))}</small><div class="copy-progress"><small>${h(s.title || s.directory)}</small>${s.summary?`<strong>${h(s.summary)}</strong>`:''}</div><div class="copy-date">${h(s.updated || 'No date stored')}</div>${renderSlots(s)}<details class="copy-details"><summary>Details</summary><code>${h(s.sha256.slice(0,12))}</code><small>Last observed ${h(when(s.last_seen))}<br>${s.version_count} content version${s.version_count===1?'':'s'} observed</small></details></div>`;
      }).join('')}</td>`;
    }).join('')}</tr>`;
  }).join('') || `<tr><td colspan="${devices.length+2}" class="empty">${groups.size?'No matching saves.':'No saves discovered yet. Scan a device to populate the table.'}</td></tr>`}</tbody></table>`;
  if (html === renderedLibrary) return;
  renderedLibrary = html;
  // Preserve open details, icons and scroll position during background refreshes.
  const iconKey = img => img.closest('tr').dataset.backend + '|' + img.closest('tr').dataset.save + '|' + img.getAttribute('src');
  const icons = new Map([...$('#library').querySelectorAll('img')].map(img => [iconKey(img), img]));
  const previous = $('#library .matrix-wrap');
  const scroll = [previous?.scrollLeft || 0,previous?.scrollTop || 0];
  const open = [...$('#library').querySelectorAll('details[open]')].map(d => d.closest('tr').dataset.backend+'|'+d.closest('tr').dataset.save+'|'+d.closest('.save-copy').querySelector('[data-id]').dataset.id);
  $('#library').innerHTML = `<div class="table-wrap matrix-wrap" tabindex="0" role="region" aria-label="Save comparison">${html}</div>`;
  const wrapper = $('#library .matrix-wrap'); wrapper.scrollLeft = scroll[0]; wrapper.scrollTop = scroll[1];
  wrapper.querySelectorAll('img').forEach(img => { const old = icons.get(iconKey(img)); if (old) img.replaceWith(old); });
  wrapper.querySelectorAll('details').forEach(d => { d.open = open.includes(d.closest('tr').dataset.backend+'|'+d.closest('tr').dataset.save+'|'+d.closest('.save-copy').querySelector('[data-id]').dataset.id); });
}
function renderCards() {
  const device = data.devices.find(d => d.id === selectedDevice);
  $('#cards-title').textContent = `${device?.name || 'Device'} · memory cards`;
  const locationIds = new Set(data.locations.filter(l => l.device_id === selectedDevice && (!scopedView() || l.backend === activeSystem)).map(l => l.id));
  const cards = data.cards.filter(c => locationIds.has(c.location_id));
  $('#cards-content').innerHTML = cards.map(c => {
    const location = locationOf(c), saves = data.saves.filter(s => s.card_id === c.id && s.present);
    return `<article><div class="save-heading"><h3>${h(c.filename)}</h3>${badge(c.present?location.status:'offline',c.present?undefined:'Missing at last scan')}</div><p class="hint">${backendOf(c).toUpperCase()} · ${h(location.name)} · ${c.formatted?`${c.free_blocks.toLocaleString()} / ${c.total_blocks.toLocaleString()} KiB free`:'Unformatted card'} · Last seen ${h(when(c.last_seen))}</p>${c.formatted?`<progress max="${c.total_blocks}" value="${c.total_blocks-c.free_blocks}" aria-label="Card capacity used"></progress>`:''}
      <div class="table-wrap"><table><thead><tr><th>Save</th><th>Directory</th><th></th></tr></thead><tbody>${saves.map(s => `<tr><td>${h(s.title || s.directory)}</td><td>${h(s.directory)}</td><td><div class="table-actions"><button data-action="transfer" data-id="${s.id}" ${canTransfer(s)?'':'disabled'}>Transfer…</button><button class="danger" data-action="delete-save" data-id="${s.id}" ${canDelete(s)?'':'disabled'}>Delete…</button></div></td></tr>`).join('') || '<tr><td colspan="3">No saves in the last known inventory.</td></tr>'}</tbody></table></div></article>`;
  }).join('') || '<div class="empty">No cards discovered yet. Add a location and scan this device.</div>';
}
function renderSync() {
  $('#sync-groups').innerHTML = data.sync_groups.filter(g => g.backend === activeSystem).map(group => {
    const members = data.sync_members.filter(m => m.group_id === group.id);
    return `<article class="sync-group"><div class="save-heading"><h2>${h(group.label)}</h2>${badge(group.enabled?group.status:'paused')}</div><p>${h(group.message || 'Waiting for stable scans.')}</p><div class="table-actions"><button data-action="configure-card-sync" data-id="${group.id}">Choose cards…</button><button data-action="toggle-sync" data-id="${group.id}">${group.enabled?'Pause sync':'Resume sync'}</button><button data-action="retry-sync" data-id="${group.id}">Scan / retry now</button></div><div class="table-wrap"><table><thead><tr><th>Memory card</th><th>Status</th><th></th></tr></thead><tbody>${members.map(member => {
      const card = data.cards.find(c => c.id === member.card_id);
      return `<tr><td>${h(cardLabel(card))}</td><td>${badge(member.status)}<small>${h(member.message)}</small></td><td><button data-action="sync-source" data-id="${group.id}" data-card="${card.id}" ${card.present && card.formatted && locationOf(card)?.status === 'online'?'':'disabled'}>Use this card…</button></td></tr>`;
    }).join('')}</tbody></table></div></article>`;
  }).join('') || '<p class="empty">No memory cards enabled for automatic sync. Create a group for each shared card.</p>';
}
function openCardSync(groupId) {
  const group = data.sync_groups.find(g => g.id === groupId);
  const backend = group?.backend || activeSystem;
  const form = $('#sync-form'); form.reset(); form.dataset.group = group?.id || '';
  form.querySelector('.form-error').textContent = '';
  form.elements.name.value = group?.label || `${systemName(backend)} shared card`;
  const chosen = group ? data.sync_members.filter(m => m.group_id === group.id).map(m => m.card_id) : [];
  $('#sync-card-choices').innerHTML = data.cards.filter(c => backendOf(c) === backend && ((c.present && canWriteCard(c)) || chosen.includes(c.id))).map(card => `<label class="checkbox"><input type="checkbox" name="card_id" value="${card.id}" ${chosen.includes(card.id)?'checked':''}> ${h(cardLabel(card))}${!card.formatted?' · blank card will be initialized':''}${locationOf(card)?.status !== 'online'?' · offline':''}</label>`).join('');
  $('#sync-dialog').showModal();
}
function openSyncSource(key,cardId) {
  const group = data.sync_groups.find(g => g.id === key), card = data.cards.find(c => c.id === cardId);
  const form = $('#sync-source-form'); form.dataset.group = key; form.dataset.card = cardId;
  form.querySelector('.form-error').textContent = '';
  $('#sync-source-summary').textContent = `${group.label} · ${cardLabel(card)}`;
  $('#sync-source-dialog').showModal();
}
function renderActivity() {
  const events = [...data.jobs.map(j => ({...j, type:'scan', label:`${j.reason === 'poll'?'Scheduled':'Device'} scan`, detail:data.devices.find(d => d.id === j.device_id)?.name})),
    ...data.transfers.map(t => ({...t,type:'transfer',label:t.action,detail:`${t.directory}${t.source_slot?' · slot '+t.source_slot+' → '+t.destination_slot:''} → ${data.cards.find(c => c.id === t.destination_card_id)?.filename || t.destination_filename || '?'}`}))].sort((a,b) => b.created.localeCompare(a.created));
  $('#activity').innerHTML = events.length ? `<div class="table-wrap"><table><thead><tr><th>Operation</th><th>Status</th><th>Started</th><th>Details</th></tr></thead><tbody>${events.map(e => `<tr><td>${h(e.label)}<small>${h(e.detail)}</small></td><td>${badge(e.status)}</td><td>${h(when(e.created))}</td><td>${e.error?`<span class="error-text">${h(e.error)}</span>`:''}${e.backup?`Backup: <code>${h(e.backup)}</code>`:''}${!e.error && !e.backup ? h(e.finished?`Finished ${when(e.finished)}`:'Awaiting completion'):''}${e.status==='preview'?`<button data-action="cancel-preview" data-id="${h(e.id)}">Cancel preview</button>`:''}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Your scans and transfers will appear here.</div>';
}
async function refresh() {
  data = await api('inventory'); applyRoute(); render();
  if ($('#notice').dataset.kind === 'connection') $('#notice').hidden = true;
}
function openDevice(id) {
  const form = $('#device-form'); form.reset(); form.dataset.id = id || ''; form.querySelector('.form-error').textContent = '';
  const device = data.devices.find(d => d.id === id);
  form.dataset.hasPassword = device?.has_ftp_password ? '1' : '';
  if (device) for (const field of ['name','root_path','kind','poll_seconds','transport','ftp_host','ftp_port','ftp_username']) {
    const input = form.elements[field];
    if (input.tagName === 'SELECT' && ![...input.options].some(o => o.value === String(device[field]))) input.add(new Option(String(device[field]),String(device[field])));
    input.value = device[field];
  }
  updateDeviceConnection();
  $('#device-form-title').textContent = device?'Device settings':'Add a device'; $('#device-dialog').showModal();
}
function updateDeviceConnection(defaults=false) {
  const form = $('#device-form'), ftp = form.elements.transport.value === 'ftp';
  $('#ftp-fields').hidden = !ftp;
  for (const name of ['ftp_host','ftp_port','ftp_username','ftp_password','clear_ftp_password']) form.elements[name].disabled = !ftp;
  for (const name of ['ftp_host','ftp_port','ftp_username']) form.elements[name].required = ftp;
  $('#ftp-clear-password').hidden = !form.dataset.hasPassword;
  form.elements.ftp_password.placeholder = form.dataset.hasPassword ? 'Leave blank to keep saved password' : 'Leave blank for anonymous access';
  $('#device-root-title').textContent = ftp ? 'Remote root folder' : 'Device root folder';
  form.elements.root_path.placeholder = ftp ? '/' : '/devices/ps2';
  $('#device-root-hint').textContent = ftp ? 'Absolute folder on the FTP server. Locations use folders relative to this root; no Docker mount is needed.' : 'Absolute path visible to the server. Mount the device folder once in Docker; locations use folders relative to this root.';
  if (defaults && ftp && !form.dataset.id) {
    if (!form.elements.root_path.value) form.elements.root_path.value = '/';
    form.elements.poll_seconds.value = '0';
  }
}
function updateLocationFormats() {
  const form = $('#location-form'), backend = form.elements.backend.value;
  const info = (data.backends || []).find(b => b.id === backend);
  const suffixes = info?.suffixes || (backend === 'ps1' ? ['.mcr','.mcd','.srm','.bin','.mem','.vm1','.vmc','.vmp'] : ['.ps2','.bin','.vm2']);
  form.elements.suffix.innerHTML = '<option value="*">All supported card files</option>' + suffixes.map(s => `<option value="${h(s)}">${h(s)} images</option>`).join('');
  form.elements.suffix.value = backend === 'ps2' ? '.ps2' : '*';
  updateLocationAccess();
}
function updateLocationRoot() {
  const form = $('#location-form');
  const deviceId = Number(form.dataset.deviceId || form.elements.device_id.value);
  const device = data.devices.find(d => d.id === deviceId);
  $('#location-root-hint').textContent = device ? `Relative to ${device.name}${device.transport === 'ftp'?' (FTP)':''}: ${device.root_path}` : 'Choose a device to see its root folder.';
  updateLocationAccess();
}
function updateLocationAccess() {
  const form = $('#location-form');
  const device = data.devices.find(d => d.id === Number(form.dataset.deviceId || form.elements.device_id.value));
  const readOnly = form.elements.suffix.value === '.vmp';
  form.elements.writable.disabled = readOnly;
  if (readOnly) form.elements.writable.checked = false;
  $('#location-write-hint').hidden = !readOnly;
}
function openLocation(deviceId, id) {
  const form = $('#location-form'); form.reset(); form.dataset.deviceId = deviceId || ''; form.dataset.id = id || ''; form.querySelector('.form-error').textContent = '';
  const location = data.locations.find(l => l.id === id);
  if (location && scopedView() && location.backend !== activeSystem) throw new Error(`Open the ${systemName(location.backend)} page to edit this location.`);
  form.elements.device_id.innerHTML = '<option value="">Choose a device…</option>' + data.devices.map(d => `<option value="${d.id}">${h(d.name)}</option>`).join('');
  form.elements.device_id.value = deviceId || '';
  form.elements.device_id.disabled = Boolean(deviceId);
  form.elements.device_id.required = !deviceId;
  $('#location-device-label').hidden = Boolean(deviceId);
  updateLocationRoot();
  form.elements.backend.innerHTML = systems().map(system => `<option value="${h(system.id)}">${h(system.name)}</option>`).join('');
  form.elements.backend.value = location?.backend || activeSystem;
  updateLocationFormats();
  form.elements.path.disabled = Boolean(location);
  form.elements.suffix.disabled = false;
  form.elements.backend.disabled = Boolean(location) || scopedView();
  if (location) {
    for (const name of ['name','path','suffix','marker']) form.elements[name].value = location[name];
    form.elements.writable.checked = Boolean(location.writable);
  }
  updateLocationAccess();
  $('#location-form-title').textContent = location?'Location settings':scopedView()?`Add a ${systemName()} location`:'Add a location'; $('#location-dialog').showModal();
}
function eligibleDestinationCards() {
  const source = data.cards.find(c => c.id === selectedSave?.card_id);
  if (!source) return [];
  const isSlot = selectedSourceSlot !== null;
  return data.cards.filter(card => {
    if (backendOf(card) !== backendOf(source)) return false;
    if (!card.present || !card.formatted || (!isSlot && card.id === source.id) || !canWriteCard(card) || locationOf(card)?.status !== 'online') return false;
    if (!isSlot) return true;
    if (selectedSave.card_slots) return selectedSave.card_slots.valid;
    const save = data.saves.find(s => s.card_id === card.id && s.directory === selectedSave.directory && s.present);
    const slots = slotsFor(save);
    return slots.length > 0 && slots.every(slot => slot.state !== 'invalid');
  });
}
function cardHasSelectedGame(card) {
  return data.saves.some(save => save.card_id === card.id && save.present &&
    (selectedSave.card_slots ? save.card_slots?.family === selectedSave.card_slots.family : save.directory === selectedSave.directory));
}
function updateDestinationCards(preserveSelection=false) {
  const form = $('#transfer-form'), select = form.elements.destination_card_id;
  const previous = preserveSelection ? select.value : '';
  const locationId = Number(form.elements.destination_location_id.value);
  const deviceId = Number(form.elements.destination_device_id.value);
  const normalize = value => value.normalize('NFKC').toLocaleLowerCase().replace(/[\s_./-]+/g,' ').trim();
  const terms = normalize(form.elements.destination_card_filter.value).split(' ').filter(Boolean);
  const available = eligibleDestinationCards().filter(card => card.location_id === locationId && deviceOf(card)?.id === deviceId);
  const cards = available.filter(card => terms.every(term => normalize(card.filename).includes(term)));
  const matches = cards.filter(cardHasSelectedGame), others = cards.filter(card => !cardHasSelectedGame(card));
  const options = group => group.sort((a,b) => a.filename.localeCompare(b.filename,undefined,{numeric:true})).map(card => `<option value="${card.id}">${h(card.filename)} · ${card.free_blocks} KiB free</option>`).join('');
  select.innerHTML = '<option value="">Choose a card…</option>' +
    (matches.length?`<optgroup label="Already contains this game">${options(matches)}</optgroup>`:'') +
    (others.length?`<optgroup label="Other cards">${options(others)}</optgroup>`:'');
  select.disabled = deletingSave || !cards.length;
  form.elements.destination_card_filter.disabled = deletingSave || !locationId;
  if (cards.some(card => String(card.id) === previous)) select.value = previous;
  else if (cards.length === 1) select.value = cards[0].id;
  $('#destination-card-hint').textContent = !locationId ? 'Choose a device and location to see its cards.' :
    `${cards.length} of ${available.length} cards${matches.length?` · ${matches.length} already contain this game`:''}${!cards.length?' · No matching cards':''}`;
  updateDestinationSlots();
}
function updateDestinationLocations() {
  const form = $('#transfer-form'), deviceId = Number(form.elements.destination_device_id.value);
  const eligible = eligibleDestinationCards().filter(card => deviceOf(card)?.id === deviceId);
  const ids = new Set(eligible.map(card => card.location_id));
  const locations = data.locations.filter(location => ids.has(location.id));
  const select = form.elements.destination_location_id;
  select.innerHTML = '<option value="">Choose a location…</option>' + locations.map(location => `<option value="${location.id}">${h(location.name)}</option>`).join('');
  select.disabled = deletingSave || !locations.length;
  if (locations.length === 1) select.value = locations[0].id;
  form.elements.destination_card_filter.value = '';
  updateDestinationCards();
}
function updateDestinationSlots() {
  if (selectedSourceSlot === null) return;
  const form = $('#transfer-form'), cardId = Number(form.elements.destination_card_id.value);
  if (!cardId) {
    form.elements.destination_slot.innerHTML = '<option value="">Choose a card first…</option>';
    form.elements.destination_slot.disabled = true;
    return;
  }
  const targetSave = data.saves.find(s => s.card_id === cardId && s.directory === selectedSave.directory && s.present);
  let slots = slotsFor(targetSave);
  if (selectedSave.card_slots) {
    const card = data.cards.find(c => c.id === cardId);
    slots = Array.from({length:selectedSave.card_slots.count}, (_,index) => {
      const number = index + 1;
      const save = data.saves.find(s => s.card_id === cardId && s.present && s.card_slots?.family === selectedSave.card_slots.family && s.card_slots.number === number);
      return {number,state:save?(save.card_slots.valid?'occupied':'invalid'):'empty',label:save?.summary || '',
              full:!save && (card?.free_blocks || 0)<selectedSave.size_blocks};
    });
  }
  const select = form.elements.destination_slot;
  select.innerHTML = '<option value="">Choose a slot…</option>' + slots.map(slot => {
    const same = cardId === selectedSave.card_id && slot.number === selectedSourceSlot;
    return `<option value="${slot.number}" ${same || slot.state==='invalid' || slot.full?'disabled':''}>Slot ${slot.number} · ${h(slot.state)}${slot.label?' · '+h(slot.label):''}${same?' (source)':''}${slot.full?' (card full)':''}</option>`;
  }).join('');
  select.disabled = !slots.length;
  const empty = slots.find(slot => slot.state === 'empty' && !slot.full);
  if (empty) select.value = empty.number;
}
function openTransfer(id, slotNumber=null, deleting=false) {
  const source = data.saves.find(s => s.id === id);
  if (source && scopedView() && saveBackend(source) !== activeSystem) throw new Error(`Open the ${systemName(saveBackend(source))} page to use this save.`);
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
  const destinations = eligibleDestinationCards();
  $('#slot-controls').hidden = !isSlot;
  form.elements.destination_slot.required = isSlot;
  form.elements.destination_slot.disabled = !isSlot;
  form.elements.conflict.innerHTML = isSlot
    ? '<option value="stop">Stop if the destination slot is occupied</option><option value="replace">Replace the selected destination slot</option>'
    : '<option value="stop">Stop and ask me</option><option value="keep_both">Keep both (supported games)</option><option value="replace">Replace destination save</option>';
  if (!isSlot && backendOf(card) === 'ps1' && !selectedSave.can_keep_both) form.elements.conflict.querySelector('[value="keep_both"]').remove();
  const deviceIds = new Set(destinations.map(card => deviceOf(card)?.id));
  const devices = data.devices.filter(device => deviceIds.has(device.id));
  form.elements.destination_device_id.innerHTML = '<option value="">Choose a device…</option>' + devices.map(device => `<option value="${device.id}">${h(device.name)}</option>`).join('');
  for (const name of ['destination_device_id','destination_location_id','destination_card_id']) form.elements[name].required = !deletingSave;
  form.elements.destination_device_id.disabled = deletingSave || !devices.length;
  if (devices.length === 1) form.elements.destination_device_id.value = devices[0].id;
  updateDestinationLocations();
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
    const id = form.dataset.id, body = {name:form.elements.name.value,root_path:form.elements.root_path.value,kind:form.elements.kind.value,transport:form.elements.transport.value,poll_seconds:Number(form.elements.poll_seconds.value)};
    if (body.transport === 'ftp') {
      Object.assign(body,{ftp_host:form.elements.ftp_host.value,ftp_port:Number(form.elements.ftp_port.value),ftp_username:form.elements.ftp_username.value});
      if (form.elements.clear_ftp_password.checked) body.ftp_password = '';
      else if (form.elements.ftp_password.value) body.ftp_password = form.elements.ftp_password.value;
    }
    const device = await api(id?`devices/${id}`:'devices',body,id?'PUT':'POST');
    $('#device-dialog').close(); await refresh();
    if (!id) openLocation(device.id);
  });
});
$('#location-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  formSubmit(form, async () => {
    const id = form.dataset.id, deviceId = form.dataset.deviceId || form.elements.device_id.value;
    const body = Object.fromEntries(['name','path','suffix','marker','backend'].map(name => [name,form.elements[name].value]));
    body.writable = form.elements.writable.checked;
    await api(`devices/${deviceId}/locations${id?`/${id}`:''}`,body,id?'PUT':'POST');
    $('#location-dialog').close(); await refresh(); notice('Location saved. Scan the device to discover its cards.');
  });
});
$('#delete-device-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  formSubmit(form, async () => {
    await api(`devices/${form.dataset.id}`,{},'DELETE');
    $('#delete-device-dialog').close();
    if (managedDevice === Number(form.dataset.id)) $('#manage-dialog').close();
    await refresh(); notice('Device removed. Library saves, files, backups, and history were kept.');
  });
});
$('#delete-location-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  formSubmit(form, async () => {
    await api(`devices/${form.dataset.deviceId}/locations/${form.dataset.id}`,{},'DELETE');
    $('#delete-location-dialog').close();
    await refresh(); notice('Location removed. Files on disk and transfer history were kept.');
  });
});
$('#new-card-sync').addEventListener('click', () => openCardSync());
$('#sync-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  formSubmit(form, async () => {
    const cardIds = [...form.querySelectorAll('[name="card_id"]:checked')].map(input => Number(input.value));
    const group = await api('sync/groups',{name:form.elements.name.value,card_ids:cardIds,...(form.dataset.group?{group_id:Number(form.dataset.group)}:{})});
    $('#sync-dialog').close(); await refresh(); switchView('sync',group.backend);
    notice(group.status === 'conflict' ? 'Sync configured. Choose the starting copy because these cards differ.' : 'Sync enabled. Watching selected devices every five minutes.');
  });
});
$('#sync-source-form').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  formSubmit(form, async () => {
    await api(`sync/groups/${form.dataset.group}/resolve`,{source_card_id:Number(form.dataset.card)});
    $('#sync-source-dialog').close(); await refresh(); notice('Source selected. Updates will sync after the cards settle.');
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
  const system = event.target.closest('[data-system]'); if (system) { event.preventDefault(); system.closest('dialog')?.close(); switchView(['saves','sync'].includes(activeView)?activeView:'library',system.dataset.system); return; }
  const nav = event.target.closest('[data-view]'); if (nav) { switchView(nav.dataset.view); return; }
  const button = event.target.closest('[data-action]'); if (!button) return;
  const id = Number(button.dataset.id);
  try {
    switch (button.dataset.action) {
      case 'game-settings': openGameSettings(id); break;
      case 'restore-library': await api(`library/slots/${id}/restore`,{version_id:Number(button.dataset.version)}); await refresh(); notice('Library version selected.'); break;
      case 'add-device': openDevice(); break;
      case 'edit-device': openDevice(id); break;
      case 'delete-device': {
        const form = $('#delete-device-form'); form.dataset.id = id;
        form.querySelector('.form-error').textContent = '';
        $('#delete-device-summary').textContent = data.devices.find(d => d.id === id).name;
        $('#delete-device-dialog').showModal(); break;
      }
      case 'manage-device': managedDevice = id; renderManage(); $('#manage-dialog').showModal(); break;
      case 'add-location': openLocation(id); break;
      case 'add-system-location': openLocation(); break;
      case 'delete-location': openDeleteLocation(id); break;
      case 'edit-location': { const l = data.locations.find(l => l.id === id); openLocation(l.device_id,id); break; }
      case 'scan': button.disabled = true; await api(`devices/${id}/scan`,{}); await refresh(); break;
      case 'cards': selectedDevice = id; renderCards(); $('#cards-dialog').showModal(); break;
      case 'transfer': openTransfer(id); break;
      case 'delete-save': openTransfer(id, null, true); break;
      case 'configure-card-sync': openCardSync(id); break;
      case 'retry-sync': await api(`sync/groups/${id}/retry`,{}); await refresh(); break;
      case 'sync-source': openSyncSource(id, Number(button.dataset.card)); break;
      case 'toggle-sync': { const group = data.sync_groups.find(g => g.id === id); await api(`sync/groups/${id}`,{enabled:!group.enabled},'PUT'); await refresh(); break; }
      case 'sort-size': librarySort = librarySort === 'size-desc' ? 'size-asc' : 'size-desc'; renderLibrary(); break;
      case 'sort-name': librarySort = librarySort === 'name-asc' ? 'name-desc' : 'name-asc'; renderLibrary(); break;
      case 'slot-copy': openTransfer(id, Number(button.dataset.slot)); break;
      case 'cancel-preview': await api(`transfers/${button.dataset.id}/cancel`,{}); await refresh(); break;
    }
  } catch (error) { notice(error.message,true); button.disabled = false; }
});
$('#add-device').addEventListener('click', () => openDevice());
$('#transfer-form').elements.destination_card_id.addEventListener('change', updateDestinationSlots);
$('#transfer-form').elements.destination_device_id.addEventListener('change', updateDestinationLocations);
$('#transfer-form').elements.destination_location_id.addEventListener('change', () => {
  $('#transfer-form').elements.destination_card_filter.value = '';
  updateDestinationCards();
});
$('#transfer-form').elements.destination_card_filter.addEventListener('input', () => updateDestinationCards(true));
$('#location-form').elements.backend.addEventListener('change', updateLocationFormats);
$('#location-form').elements.suffix.addEventListener('change', updateLocationAccess);
$('#location-form').elements.device_id.addEventListener('change', updateLocationRoot);
$('#device-form').elements.transport.addEventListener('change', () => updateDeviceConnection(true));
window.addEventListener('hashchange', () => { applyRoute(); render(); });
window.addEventListener('popstate', () => { applyRoute(); render(); });
$('#search').addEventListener('input', renderLibrary);
$('#animate-icons').checked = !matchMedia('(prefers-reduced-motion: reduce)').matches;
$('#animate-icons').addEventListener('change', renderLibrary);
refresh().catch(error => notice(`Could not load the library: ${error.message}`,true,'connection'));
setInterval(() => { if (!document.hidden) refresh().catch(error => notice(`Connection interrupted: ${error.message}`,true,'connection')); }, 4000);

$('#saved-search').addEventListener('input',renderSavedLibrary);
$('#library-settings-form').addEventListener('submit',event => {
  event.preventDefault(); const form = event.currentTarget;
  formSubmit(form,async () => {
    await api('library/settings',{previous_versions:Number(form.elements.previous_versions.value)},'PUT');
    await refresh(); notice('Library history settings saved.');
  });
});

$('#game-settings-form').addEventListener('submit',event => {
  event.preventDefault(); const form = event.currentTarget;
  formSubmit(form,async () => {
    await api(`library/games/${form.dataset.id}`,{card_ports:form.elements.card_ports.value,card_port_note:form.elements.card_port_note.value},'PUT');
    $('#game-settings-dialog').close(); await refresh(); notice('Game settings saved.');
  });
});
