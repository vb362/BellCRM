/* Original shell interactions plus the embedded CRM review sizing bridge. */
(() => {
  const main = document.querySelector('main');
  // Review text is real content; no placeholder overlays are applied.
  const layoutCopy = () => {};
  const reviewFrame = document.getElementById('crm-content');
  window.addEventListener('message', event => {
    if (event.source !== reviewFrame?.contentWindow) return;
    if (event.data?.type === 'crm-content-height') {
      const height = Number(event.data.height);
      if (Number.isFinite(height) && height > 0 && height < 30000) reviewFrame.style.height = `${height}px`;
    }
    if (event.data?.type === 'crm-review-scroll-top') scroller?.scrollTo({top:0});
    if (event.data?.type === 'crm-modal') {
      modalOpen = !!event.data.open;
      controlsHost.style.visibility = modalOpen ? 'hidden' : '';
      syncModal();
    }
    if (modalOpen && ['crm-content-height', 'crm-controls-update'].includes(event.data?.type)) syncModal();
    if (event.data?.type === 'crm-navigate' && event.data.view === 'decisions') showView('decisions');
    if (['crm-content-height', 'crm-controls-update'].includes(event.data?.type)) scheduleReviewControls();
  });
  const wrapper = document.querySelector('[data-sidebar-state]');
  const sidebar = document.querySelector('[data-visual-state]');
  const toggle = document.querySelector('[data-app-shell-sidebar-toggle]');
  const reveal = toggle.parentElement;
  const revealClass = reveal.className;
  const switcher = document.querySelector('[data-app-shell-sidebar-app-switcher]');
  const switcherClass = switcher.className;
  let visualTimer;
  function setExpanded(expanded) {
    clearTimeout(visualTimer);
    const state = expanded ? 'expanded' : 'collapsed';
    wrapper.dataset.sidebarState = state;
    sidebar.dataset.state = state;
    sidebar.dataset.collapsible = expanded ? '' : 'icon';
    const finishVisual = () => {
      sidebar.dataset.visualState = state;
      sidebar.dataset.visualCollapsible = expanded ? '' : 'icon';
      switcher.className = expanded ? 'min-w-8 overflow-hidden transition-opacity will-change-[opacity] flex flex-1' : switcherClass;
    };
    if (expanded) finishVisual(); else visualTimer = setTimeout(finishVisual, 200);
    reveal.className = expanded ? 'flex shrink-0 items-center' : revealClass;
    toggle.setAttribute('aria-label', expanded ? 'Replier la barre latérale' : 'Déplier la barre latérale');
    toggle.setAttribute('aria-expanded', String(expanded));
  }
  toggle.addEventListener('click', () => setExpanded(sidebar.dataset.state !== 'expanded'));
  for (const trigger of sidebar.querySelectorAll('button[aria-controls]')) {
    const panel = document.getElementById(trigger.getAttribute('aria-controls'));
    if (!panel) continue;
    trigger.addEventListener('click', () => {
      if (sidebar.dataset.state === 'collapsed') setExpanded(true);
      const expanded = trigger.getAttribute('aria-expanded') !== 'true';
      trigger.setAttribute('aria-expanded', String(expanded));
      trigger.dataset.state = expanded ? 'open' : 'closed';
      panel.inert = !expanded;
      panel.style.gridTemplateRows = expanded ? '1fr' : '0fr';
    });
  }
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'b') {
      event.preventDefault();
      setExpanded(sidebar.dataset.state !== 'expanded');
    }
  });
  const scroller = main.querySelector('[class*="scrollbar-gutter:stable"]');
  if (scroller) scroller.style.overflowAnchor = 'none';
  const sticky = main.querySelector('[aria-hidden="true"][inert]');
  const header = main.querySelector('header');
  const sentinel = header?.querySelector('[aria-hidden="true"].absolute');
  // Render the small controls in the host's scrolling context. Their positioning
  // is entirely native CSS sticky, with no per-scroll JavaScript compensation.
  const controlsHost = document.createElement('div');
  controlsHost.id = 'crm-sticky-controls';
  controlsHost.style.cssText = 'position:absolute;inset:0;z-index:2;pointer-events:none';
  reviewFrame.parentElement.append(controlsHost);
  const controlsRoot = controlsHost.attachShadow({mode:'open'});
  controlsRoot.innerHTML = `<style>
    :host { -webkit-font-smoothing:antialiased; }
    * { box-sizing:border-box; }
    .track { position:absolute; bottom:0; pointer-events:none; }
    .pinned { position:sticky; top:64px; pointer-events:auto; background:var(--crm-surface); }
    .pinned::before { content:''; position:absolute; inset:-12px 0 auto; height:12px; background:var(--crm-surface); pointer-events:none; }
    .pinned.heading::after { content:''; position:absolute; top:-12px; bottom:0; right:100%; width:44px; background:var(--crm-surface); pointer-events:none; }
    .filters { max-height:var(--crm-filter-height); overflow-y:auto; scrollbar-width:thin; }

    button:focus-visible { outline:2px solid #fa500f; outline-offset:-2px; }
  </style>`;
  const hostedControls = new Map();
  let modalOpen = false;
  const modalLayer = document.createElement('div');
  modalLayer.id = 'crm-modal-layer';
  modalLayer.style.cssText = 'position:fixed;inset:0;z-index:9999;display:none';
  document.body.append(modalLayer);
  const modalRoot = modalLayer.attachShadow({mode:'open'});
  const modalBlur = document.createElement('style');
  modalBlur.textContent = 'body.crm-modal-open > *:not(#crm-modal-layer) { filter:blur(3px) saturate(.9); pointer-events:none; }';
  document.head.append(modalBlur);
  function syncModal() {
    const doc = reviewFrame?.contentDocument;
    const src = doc?.querySelector('.submit-overlay');
    if (!modalOpen || !src) {
      modalLayer.style.display = 'none';
      modalRoot.replaceChildren();
      document.body.classList.remove('crm-modal-open');
      return;
    }
    const styles = document.createElement('style');
    styles.textContent = [...doc.querySelectorAll('style')].map(s => s.textContent).join('\n') +
      '\n.submit-overlay { display:flex !important; position:fixed; inset:0; align-items:flex-start; justify-content:center; padding:64px 20px 40px; background:rgba(40,35,22,.22); }' +
      '\n:host { font-family:Inter, system-ui, sans-serif; color:#242320; }';
    const clone = src.cloneNode(true);
    clone.querySelectorAll('button').forEach(btn => {
      const sel = '[data-action="' + btn.dataset.action + '"]' + (btn.dataset.id ? '[data-id="' + btn.dataset.id + '"]' : '');
      btn.addEventListener('click', () => doc.querySelector(sel)?.click());
    });
    clone.querySelectorAll('[data-recovery-id]').forEach(input => {
      input.addEventListener('input', () => {
        const original=doc.querySelector('[data-recovery-id="'+input.dataset.recoveryId+'"]');
        if(original){original.value=input.value;original.dispatchEvent(new doc.defaultView.Event('input',{bubbles:true}));}
      });
    });
    modalRoot.replaceChildren(styles, clone);
    modalLayer.style.display = '';
    document.body.classList.add('crm-modal-open');
  }
  let controlFrame;
  let copiedFonts = false;
  function scheduleReviewControls() {
    cancelAnimationFrame(controlFrame);
    controlFrame = requestAnimationFrame(layoutReviewControls);
  }
  function layoutReviewControls() {
    // The review iframe stays mounted, but its controls must not cover Welcome.
    if (document.getElementById('crm-review-panel').hidden) {
      controlsHost.hidden = true;
      return;
    }
    const doc = reviewFrame?.contentDocument;
    const grid = doc?.querySelector('.crm-home-grid');
    controlsHost.hidden = !grid;
    controlsHost.style.visibility = modalOpen ? 'hidden' : '';
    if (!grid || !scroller) return;
    if (!copiedFonts) {
      const fonts = document.createElement('style');
      fonts.textContent = [...doc.querySelectorAll('style')].map(s => (s.textContent.match(/@font-face\s*\{[^}]*\}/g) || []).join('\n')).join('\n');
      document.head.append(fonts);
      const reviewStyles = document.createElement('style');
      reviewStyles.textContent = doc.querySelector('style[data-review-styles]')?.textContent || '';
      controlsRoot.append(reviewStyles);
      copiedFonts = true;
    }
    const bodyStyle = doc.defaultView.getComputedStyle(doc.body);
    controlsHost.style.font = bodyStyle.font;
    controlsHost.style.color = bodyStyle.color;
    const surface = getComputedStyle(main.querySelector('.bg-subtle') || main).backgroundColor;
    controlsHost.style.setProperty('--crm-surface', surface);
    controlsHost.style.setProperty('--crm-filter-height', `${Math.max(120, scroller.clientHeight - 76)}px`);
    const twoColumns = doc.defaultView.getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).length > 1;
    for (const [key, selector, enabled] of [['filters', '.crm-filters', twoColumns], ['heading', '.crm-list-heading', true]]) {
      const source = grid.querySelector(selector);
      let item = hostedControls.get(key);
      if (!item) {
        const track = document.createElement('div');
        track.className = 'track';
        const pinned = document.createElement('div');
        pinned.className = `pinned ${key}`;
        track.append(pinned);
        controlsRoot.append(track);
        item = {track, pinned};
        hostedControls.set(key, item);
      }
      item.track.hidden = !enabled;
      source.toggleAttribute('data-crm-hosted', enabled);
      source.inert = enabled;
      if (!enabled) continue;
      const rect = source.getBoundingClientRect();
      item.track.style.cssText = `left:${rect.left}px;top:${rect.top}px;width:${rect.width}px`;
      const markup = source.outerHTML;
      if (item.markup !== markup || item.source !== source) {
        const focusedIndex = [...item.pinned.querySelectorAll('button')].indexOf(controlsRoot.activeElement);
        const clone = source.cloneNode(true);
        clone.removeAttribute('data-crm-hosted');
        clone.inert = false;
        const originals = [...source.querySelectorAll('button')];
        clone.querySelectorAll('button').forEach((button, i) => button.addEventListener('click', () => originals[i].click()));
        const originalSelects=[...source.querySelectorAll('select')];
        clone.querySelectorAll('select').forEach((select,i)=>select.addEventListener('change',()=>{originalSelects[i].value=select.value;originalSelects[i].dispatchEvent(new doc.defaultView.Event('change',{bubbles:true}));}));
        item.pinned.replaceChildren(clone);
        if (focusedIndex >= 0) item.pinned.querySelectorAll('button')[focusedIndex]?.focus({preventScroll:true});
        item.markup = markup;
        item.source = source;
      }
    }
  }
  reviewFrame?.addEventListener('load', scheduleReviewControls);
  window.addEventListener('resize', scheduleReviewControls);
  if (scroller) new ResizeObserver(scheduleReviewControls).observe(scroller);
  if (scroller && sticky && header) {
    scroller.addEventListener('scroll', () => {
      const shown = (sentinel || header).getBoundingClientRect().bottom < scroller.getBoundingClientRect().top + 52;
      sticky.classList.toggle('pointer-events-none', !shown);
      sticky.classList.toggle('-translate-y-full', !shown);
      sticky.classList.toggle('opacity-0', !shown);
      sticky.inert = !shown;
      sticky.setAttribute('aria-hidden', String(!shown));
      layoutCopy();
    }, { passive: true });
  }
  // Exact values from the original action component: x 0 -> 4, .2s easeOut.
  for (const row of main.querySelectorAll('div.flex.items-center.justify-between.rounded-sm.p-3')) {
    const arrow = row.lastElementChild;
    if (!arrow?.querySelector('svg')) continue;
    const animate = x => {
      const from = getComputedStyle(arrow).transform;
      arrow.getAnimations().forEach(animation => animation.cancel());
      arrow.animate([{transform:from}, {transform:`translateX(${x}px)`}], {
        duration:200, easing:'cubic-bezier(0,0,0.58,1)', fill:'forwards'
      });
    };
    row.addEventListener('pointerenter', () => animate(4));
    row.addEventListener('pointerleave', () => animate(0));
  }
  const resizer = document.querySelector('[role="separator"]');
  if (resizer) {
    const resize = width => {
      const clamped = Math.max(128, Math.min(480, width));
      wrapper.style.setProperty('--sidebar-width', `${clamped}px`);
      resizer.setAttribute('aria-valuenow', String(clamped));
    };
    resizer.addEventListener('pointerdown', event => {
      setExpanded(true);
      sidebar.dataset.resizing = 'true';
      sidebar.dataset.resizePhase = 'live';
      resizer.setPointerCapture(event.pointerId);
    });
    resizer.addEventListener('pointermove', event => {
      if (resizer.hasPointerCapture(event.pointerId)) resize(event.clientX);
    });
    resizer.addEventListener('pointerup', event => {
      if (resizer.hasPointerCapture(event.pointerId)) resizer.releasePointerCapture(event.pointerId);
      sidebar.dataset.resizing = 'false';
      sidebar.dataset.resizePhase = 'idle';
    });
    resizer.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); setExpanded(true);
      const width = Number(resizer.getAttribute('aria-valuenow'));
      resize(event.key === 'Home' ? 128 : event.key === 'End' ? 480 : width + (event.key === 'ArrowRight' ? 8 : -8));
    });
  }
  // Mode selection is required on each page load.
  let selectedMode = null;
  const welcomePanel = document.getElementById('crm-welcome');
  const reviewPanel = document.getElementById('crm-review-panel');
  const pageTitle = document.getElementById('crm-page-title');
  const pageDescription = document.getElementById('crm-page-description');
  const modeLabel = document.querySelector('[data-crm-app-label] > span');
  const modeNames = {test:'Test mode', production:'Production mode'};

  function showView(view, moveFocus = true) {
    if (selectedMode === null) view = 'welcome';
    // Every screen is gated until Test mode has opened a server session.
    const runs = view === 'runs';
    const decisions = view === 'decisions';
    const sources = view === 'sources';
    const settings = view === 'settings';
    const welcome = view === 'welcome' || (!runs && !decisions && !sources && !settings && selectedMode === null);
    welcomePanel.hidden = !welcome;
    reviewPanel.hidden = welcome || runs || sources || settings;
    document.getElementById('crm-runs').hidden = !runs;
    document.getElementById('crm-sources').hidden = !sources;
    document.getElementById('crm-settings').hidden = !settings;
    document.getElementById('runs-new-button').hidden = !runs;
    const title = decisions ? 'Decisions' : settings ? 'Settings' : sources ? 'Sources' : runs ? 'Runs' : welcome ? 'Welcome' : 'Edit Proposal';
    pageTitle.textContent = title;
    document.getElementById('crm-sticky-title').textContent = title;
    pageDescription.textContent = decisions ? 'Your review decisions and CRM changes.' : settings ? 'Manage your local settings.' : sources ? 'View your sources of external data for CRM edits' : runs ? 'Manage your past runs.' : welcome
      ? 'Choose how you want to work with Bellhaven.'
      : `${modeNames[selectedMode]} · Compare source data, review suggested changes, and submit your decisions.`;
    document.title = `${title} — Bellhaven CRM`;
    document.querySelectorAll('[data-sidebar="menu-button"]').forEach(link => {
      const active = link.getAttribute('href') === (decisions ? '#decisions' : settings ? '#settings' : sources ? '#sources' : runs ? '#runs' : welcome ? '#welcome' : '#home');
      link.dataset.active = String(active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    if (decisions) reviewFrame.contentWindow.postMessage({type:'crm-decisions'}, '*');
    else if (!welcome && !runs && !sources && !settings) reviewFrame.contentWindow.postMessage({type:'crm-home'}, '*');
    scroller?.scrollTo({top:0});
    scheduleReviewControls();
    if (moveFocus) pageTitle.focus({preventScroll:true});
  }

  welcomePanel.querySelectorAll('[data-mode]').forEach(card => {
    card.addEventListener('click', async () => {
      card.disabled=true;
      try {
        await window.Bellhaven.select(card.dataset.mode);
        selectedMode=card.dataset.mode;
        modeLabel.textContent=modeNames[selectedMode];
        document.querySelector('[aria-label="Open app switcher (CRM)"]')?.setAttribute('aria-label','Current mode');
        document.querySelectorAll('[data-sidebar="menu-button"]').forEach(link=>link.removeAttribute('aria-disabled'));
        showView('runs');
      } catch(error) {window.Bellhaven.error(error.message);}
      finally {card.disabled=location.protocol==='file:';}
    });
  });
  document.querySelectorAll('[data-sidebar="menu-button"]').forEach(link=>{if(link.getAttribute('href')!=='#welcome')link.setAttribute('aria-disabled','true');});
  document.addEventListener('crm-show-view',event=>showView(event.detail));
  for (const view of ['welcome', 'home', 'runs', 'decisions', 'sources', 'settings']) {
    document.querySelector(`[data-sidebar="menu-button"][href="#${view}"]`)
      ?.addEventListener('click', event => {
        event.preventDefault();
        showView(view);
      });
  }
  document.getElementById('sidebar-view-runs').addEventListener('click', event => {
    event.preventDefault();
    document.dispatchEvent(new CustomEvent('crm-show-all-runs'));
    showView('runs');
  });
  // UI preview only: both answers close the native dialog without resetting data.
  const resetDialog = document.getElementById('settings-reset-dialog');
  const resetButton = document.getElementById('settings-reset-button');
  resetButton.addEventListener('click', () => resetDialog.showModal());
  resetDialog.addEventListener('close', () => resetButton.focus({preventScroll:true}));
  showView('welcome', false);

  // A visual replica has no authenticated Mistral account actions.
  document.querySelectorAll('a').forEach(link => link.addEventListener('click', event => event.preventDefault()));
})();
