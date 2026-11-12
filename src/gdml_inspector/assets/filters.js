// Shared object filter model and panel (injected into both viewers), so the
// same filter selects the same placements in standalone and lazy exports.
//
// Semantics (also in docs/limitations.md):
//   * Categories combine with AND: search, exclude, solid type, materials.
//   * Within materials, a placement is included unless its material is
//     switched off. `exclude` beats everything: a match is dropped even if the
//     other categories include it.
//   * "Invert selection" complements the final result.
//   * Matching is literal and case-insensitive substring matching on the
//     original identifiers (Geant4 address suffixes kept) and their semantic
//     (suffix-stripped) forms. A term may be scoped with a prefix:
//     material:  volume:  name:  path:  solid:  type:   e.g. `path:world/group`.
//     Anything else, including an unknown `prefix:`, is a plain substring.
//   * The filter never touches geometry: it is evaluated from placement
//     metadata, so it works for layers that are not loaded.

const FILTER_FIELDS = {
  material: ['material'],
  volume: ['volume', 'semanticVolume'],
  name: ['name', 'semanticName'],
  path: ['path'],
  solid: ['solid'],
  type: ['solidType'],
};
const FILTER_ANY_FIELDS = ['path', 'name', 'semanticName', 'volume', 'semanticVolume', 'material', 'solid', 'solidType'];

function parseFilterTerm(text) {
  const trimmed = text.trim();
  const colon = trimmed.indexOf(':');
  if (colon > 0) {
    const scope = trimmed.slice(0, colon).toLowerCase();
    if (Object.prototype.hasOwnProperty.call(FILTER_FIELDS, scope)) {
      return {fields: FILTER_FIELDS[scope], scope, needle: trimmed.slice(colon + 1).trim().toLowerCase()};
    }
  }
  return {fields: FILTER_ANY_FIELDS, scope: null, needle: trimmed.toLowerCase()};
}
function filterTermMatches(term, object) {
  if (term.needle === '') return true;
  return term.fields.some(field => typeof object[field] === 'string' && object[field].toLowerCase().includes(term.needle));
}

function emptyFilterState() {
  return {search: '', exclude: '', solidType: '', disabledMaterials: [], invert: false};
}
function filterIsActive(state) {
  return state.search.trim() !== '' || state.exclude.trim() !== '' || state.solidType !== ''
    || state.disabledMaterials.length > 0 || state.invert;
}
// Returns a predicate over object records (path, name, material, solid, ...).
function compileFilter(state) {
  const search = parseFilterTerm(state.search);
  const exclude = parseFilterTerm(state.exclude);
  const excluding = exclude.needle !== '';
  const off = new Set(state.disabledMaterials);
  return object => {
    const included = filterTermMatches(search, object)
      && (state.solidType === '' || object.solidType === state.solidType)
      && !off.has(object.material)
      && !(excluding && filterTermMatches(exclude, object));
    return state.invert ? !included : included;
  };
}
function describeFilter(state, materialCount) {
  const parts = [];
  if (state.search.trim()) parts.push(`search "${state.search.trim()}"`);
  if (state.exclude.trim()) parts.push(`exclude "${state.exclude.trim()}"`);
  if (state.solidType) parts.push(`solid type ${state.solidType}`);
  if (state.disabledMaterials.length) parts.push(`${materialCount - state.disabledMaterials.length} of ${materialCount} materials on`);
  if (state.invert) parts.push('inverted');
  return parts;
}

// materials: [{name, count, color: 'css colour'}]; solidTypes: [names].
// onChange(state) fires on every user edit. Returns the panel's API.
function buildFilterPanel(root, {materials, solidTypes, onChange}) {
  root.innerHTML = `
    <input id="search" placeholder="Search name, path, material, solid... (or material: volume: name: path: solid: type:)">
    <input id="excludeSearch" placeholder="Exclude matching...">
    <select id="solidType"></select>
    <label class="row"><input id="invertFilter" type="checkbox"> Invert selection</label>
    <div class="small-actions"><button id="allMaterials" type="button">All materials</button><button id="noMaterials" type="button">No materials</button></div>
    <div id="materials" class="material-list"></div>
    <div id="filterSummary" class="muted"></div>
    <div id="filterCounts" class="muted"></div>
    <div id="filterEmpty" class="warn" style="display:none"></div>
    <button id="resetFilters" type="button">Reset all filters</button>`;
  const $ = id => root.querySelector('#' + id);
  const materialNames = materials.map(m => m.name);
  const enabled = Object.fromEntries(materialNames.map(name => [name, true]));
  $('solidType').innerHTML = `<option value="">All solid types</option>`
    + solidTypes.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');

  function state() {
    return {
      search: $('search').value,
      exclude: $('excludeSearch').value,
      solidType: $('solidType').value,
      disabledMaterials: materialNames.filter(name => enabled[name] === false),
      invert: $('invertFilter').checked,
    };
  }
  function renderMaterials() {
    $('materials').innerHTML = materials.map(m => `
      <label class="material-item">
        <input type="checkbox" data-material="${escapeHtml(m.name)}" ${enabled[m.name] ? 'checked' : ''}>
        <span class="swatch" style="background:${m.color}"></span>
        <span title="${escapeHtml(m.name)}">${escapeHtml(m.name)}</span>
        <span>${m.count.toLocaleString()}</span>
      </label>`).join('');
    $('materials').querySelectorAll('input[data-material]').forEach(input => {
      input.addEventListener('change', () => { enabled[input.dataset.material] = input.checked; changed(); });
    });
  }
  let lastCounts = null;
  function renderSummary() {
    const s = state();
    const parts = describeFilter(s, materialNames.length);
    $('filterSummary').textContent = parts.length
      ? `Active (all must hold): ${parts.join(' AND ')}` : 'No filters active.';
    $('resetFilters').disabled = !filterIsActive(s);
    const counts = lastCounts;
    const empty = $('filterEmpty');
    if (!counts) { $('filterCounts').textContent = ''; empty.style.display = 'none'; return; }
    $('filterCounts').textContent = `${counts.matched.toLocaleString()} of ${counts.total.toLocaleString()} match · ${counts.shown.toLocaleString()} shown`;
    if (counts.matched === 0) {
      empty.textContent = parts.length
        ? `Nothing matches. These filters combine with AND, and exclude removes matches: ${parts.join(' AND ')}. Reset or loosen one of them.`
        : 'There are no objects to match.';
      empty.style.display = '';
    } else if (counts.shown === 0) {
      empty.textContent = counts.hiddenReason || 'Matching objects are hidden (by the hierarchy, or their layers are not loaded).';
      empty.style.display = '';
    } else empty.style.display = 'none';
  }
  function changed() { renderSummary(); onChange(state()); }
  for (const id of ['search', 'excludeSearch', 'solidType', 'invertFilter']) {
    $(id).addEventListener('input', changed);
  }
  $('allMaterials').onclick = () => { materialNames.forEach(n => { enabled[n] = true; }); renderMaterials(); changed(); };
  $('noMaterials').onclick = () => { materialNames.forEach(n => { enabled[n] = false; }); renderMaterials(); changed(); };

  // Replaces the whole filter from a (validated) state. Returns the
  // identifiers it could not honour so the caller can report them.
  function set(next) {
    const unknown = [];
    $('search').value = next.search || '';
    $('excludeSearch').value = next.exclude || '';
    $('invertFilter').checked = Boolean(next.invert);
    materialNames.forEach(n => { enabled[n] = true; });
    for (const name of next.disabledMaterials || []) {
      if (name in enabled) enabled[name] = false;
      else unknown.push({kind: 'material', value: name});
    }
    const select = $('solidType');
    if (next.solidType && ![...select.options].some(o => o.value === next.solidType)) {
      unknown.push({kind: 'solid type', value: next.solidType});
      select.value = '';
    } else select.value = next.solidType || '';
    renderMaterials(); renderSummary();
    return unknown;
  }
  function reset() { set(emptyFilterState()); changed(); }
  $('resetFilters').onclick = reset;
  // counts: {total, matched, shown, hiddenReason?} or null while unknown.
  function setCounts(counts) { lastCounts = counts; renderSummary(); }

  renderMaterials(); renderSummary();
  return {state, set, reset, setCounts, materialNames};
}
