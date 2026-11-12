// Shared physical-volume hierarchy tree (injected into both viewers).
//
// Renders the flat pre-order placement list (HierarchyNodeDict: path,
// parentPath, name, semanticName/Volume, rendered, isAssembly, objectId, ...)
// as an expandable, keyboard-navigable, searchable tree. Unexpanded subtrees
// never reach the DOM, so a large tree costs only what the user opens.
//
//   const tree = createHierarchyTree(document.getElementById('hierarchyTree'), {
//     nodes, worldPath,
//     selected: () => selectedPath,        // current selection (for the highlight)
//     onSelect: path => ...,               // a row was clicked / activated
//     actions: [{label: 'Focus', onClick: path => ...}],
//     checkbox: {onChange: (path, checked) => ...},   // optional show/hide box
//     labelExtra: node => '<html>',        // appended to the label (trusted/escaped)
//     searchExtra: node => 'text',         // extra searchable text
//     decorateRow: (path, entry, node) => {},  // per-row state on every refresh
//     afterRefresh: () => {},
//   });
//   tree.render(); tree.refreshStates(); tree.reveal(path); tree.search(query);
function createHierarchyTree(root, options) {
  const {
    nodes, worldPath, selected, onSelect, actions = [], checkbox = null,
    labelExtra = () => '', searchExtra = () => '', decorateRow = () => {}, afterRefresh = () => {},
  } = options;
  const nodeByPath = Object.fromEntries(nodes.map(n => [n.path, n]));
  const childrenByPath = {};
  for (const node of nodes) {
    if (node.parentPath !== null) (childrenByPath[node.parentPath] ||= []).push(node.path);
  }
  const expandedPaths = options.expandedPaths || new Set([worldPath]);
  const rowElements = new Map();
  let lastFocusedPath = null;

  function hasChildren(path) { return (childrenByPath[path] || []).length > 0; }
  function subtreeObjectIds(path) {
    const out = [];
    const node = nodeByPath[path];
    if (node && node.rendered && node.objectId !== undefined) out.push(node.objectId);
    for (const child of (childrenByPath[path] || [])) out.push(...subtreeObjectIds(child));
    return out;
  }
  function visibleRows() { return Array.from(root.querySelectorAll('.tree-row')); }
  function setRovingTabIndex(path) {
    for (const row of visibleRows()) row.tabIndex = -1;
    const entry = rowElements.get(path);
    if (entry) { entry.row.tabIndex = 0; lastFocusedPath = path; }
  }
  function focusRow(path) {
    setRovingTabIndex(path);
    const entry = rowElements.get(path);
    if (entry) entry.row.focus();
  }

  function createRow(path, depth) {
    const node = nodeByPath[path];
    const row = document.createElement('div');
    row.className = 'tree-row';
    row.style.paddingLeft = (depth * 14) + 'px';
    row.setAttribute('role', 'treeitem');
    row.dataset.path = path;
    row.tabIndex = -1;
    if (hasChildren(path)) row.setAttribute('aria-expanded', expandedPaths.has(path) ? 'true' : 'false');

    const toggle = document.createElement('span');
    toggle.className = 'tree-toggle';
    if (hasChildren(path)) {
      toggle.textContent = expandedPaths.has(path) ? '▾' : '▸';
      toggle.addEventListener('click', e => { e.stopPropagation(); toggleExpand(path); });
    }

    let check = null;
    if (checkbox) {
      check = document.createElement('input');
      check.type = 'checkbox';
      check.title = 'Show/hide this subtree';
      check.addEventListener('click', e => e.stopPropagation());
      check.addEventListener('change', () => checkbox.onChange(path, check.checked));
    }

    const label = document.createElement('span');
    label.className = 'tree-label';
    const differs = node.semanticVolume && node.semanticVolume !== node.name;
    label.innerHTML = escapeHtml(node.name)
      + (differs ? ` <span class="tree-muted">(${escapeHtml(node.semanticVolume)})</span>` : '')
      + (!node.rendered ? ` <span class="tree-muted">${node.isAssembly ? '(assembly)' : '(container)'}</span>` : '')
      + labelExtra(node);

    const actionBox = document.createElement('span');
    actionBox.className = 'tree-actions';
    for (const action of actions) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'tree-action-btn'; button.textContent = action.label;
      button.addEventListener('click', e => { e.stopPropagation(); action.onClick(path); });
      actionBox.append(button);
    }

    row.append(toggle, ...(check ? [check] : []), label, actionBox);
    row.addEventListener('click', () => {
      onSelect(path);
      focusRow(path);
      refreshStates();
    });
    row.addEventListener('keydown', e => handleKeydown(e, path));
    rowElements.set(path, {row, check, toggle, label});
    return row;
  }

  function renderChildren(container, path, depth) {
    for (const childPath of (childrenByPath[path] || [])) {
      container.appendChild(createRow(childPath, depth));
      if (expandedPaths.has(childPath)) {
        const sub = document.createElement('div');
        sub.className = 'tree-children';
        container.appendChild(sub);
        renderChildren(sub, childPath, depth + 1);
      }
    }
  }
  function render() {
    root.setAttribute('role', 'tree');
    root.innerHTML = '';
    rowElements.clear();
    root.appendChild(createRow(worldPath, 0));
    if (expandedPaths.has(worldPath)) {
      const sub = document.createElement('div');
      sub.className = 'tree-children';
      root.appendChild(sub);
      renderChildren(sub, worldPath, 1);
    }
    setRovingTabIndex(rowElements.has(lastFocusedPath) ? lastFocusedPath : worldPath);
    refreshStates();
  }
  function toggleExpand(path) {
    if (expandedPaths.has(path)) expandedPaths.delete(path); else expandedPaths.add(path);
    render();
    focusRow(path);
  }
  function refreshStates() {
    const current = selected();
    for (const [path, entry] of rowElements) {
      entry.row.classList.toggle('tree-selected', path === current);
      decorateRow(path, entry, nodeByPath[path]);
    }
    afterRefresh();
  }
  function handleKeydown(e, path) {
    const rows = visibleRows();
    const index = rows.findIndex(r => r.dataset.path === path);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (index < rows.length - 1) focusRow(rows[index + 1].dataset.path);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (index > 0) focusRow(rows[index - 1].dataset.path);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      if (hasChildren(path)) {
        if (!expandedPaths.has(path)) toggleExpand(path);
        else { const kids = childrenByPath[path]; if (kids && kids.length) focusRow(kids[0]); }
      }
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      if (expandedPaths.has(path) && hasChildren(path)) toggleExpand(path);
      else { const node = nodeByPath[path]; if (node.parentPath) focusRow(node.parentPath); }
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      const entry = rowElements.get(path);
      if (entry.check) {
        entry.check.checked = !entry.check.checked;
        entry.check.dispatchEvent(new Event('change'));
      } else {
        onSelect(path);
        refreshStates();
      }
    }
  }

  function expandAncestors(path) {
    const node = nodeByPath[path];
    let p = node ? node.parentPath : null;
    while (p) { expandedPaths.add(p); const parent = nodeByPath[p]; p = parent ? parent.parentPath : null; }
  }
  // Expands every ancestor of `path`, redraws and scrolls its row into view.
  function reveal(path) {
    if (!nodeByPath[path]) return;
    expandAncestors(path);
    render();
    const entry = rowElements.get(path);
    if (entry) entry.row.scrollIntoView({block: 'nearest'});
  }
  function matches(node, q) {
    return node.name.toLowerCase().includes(q)
      || node.semanticVolume.toLowerCase().includes(q)
      || node.path.toLowerCase().includes(q)
      || searchExtra(node).toLowerCase().includes(q);
  }
  // Empty query: plain redraw. Otherwise expand to every match and dim the rest.
  function search(query) {
    const q = query.toLowerCase().trim();
    if (!q) { render(); return; }
    const found = nodes.filter(n => matches(n, q));
    for (const match of found) expandAncestors(match.path);
    render();
    const foundPaths = new Set(found.map(n => n.path));
    for (const [path, {row}] of rowElements) {
      row.classList.toggle('tree-match', foundPaths.has(path));
      row.classList.toggle('tree-dim', !foundPaths.has(path));
    }
  }

  return {
    nodeByPath, childrenByPath, expandedPaths, rowElements, hasChildren, subtreeObjectIds,
    render, refreshStates, toggleExpand, reveal, search, focusRow,
  };
}

// Bounding box (min xyz, max xyz) of a list of object records, or null.
function unionBounds(objects) {
  let box = null;
  for (const object of objects) {
    const b = object.bbox;
    if (!box) box = b.slice();
    else {
      box[0] = Math.min(box[0], b[0]); box[1] = Math.min(box[1], b[1]); box[2] = Math.min(box[2], b[2]);
      box[3] = Math.max(box[3], b[3]); box[4] = Math.max(box[4], b[4]); box[5] = Math.max(box[5], b[5]);
    }
  }
  return box;
}

// Camera target for "focus on this placement": the union bounds of its
// rendered subtree, walking up to the nearest ancestor that has any (a
// container with no rendered descendants would otherwise give an empty box).
// Returns the box, or null when nothing along the path is rendered.
function focusBounds(tree, objectsById, path) {
  let probe = path;
  let box = unionBounds(tree.subtreeObjectIds(probe).map(id => objectsById(id)));
  while (!box) {
    const node = tree.nodeByPath[probe];
    if (!node || node.parentPath === null) break;
    probe = node.parentPath;
    box = unionBounds(tree.subtreeObjectIds(probe).map(id => objectsById(id)));
  }
  return box;
}
