(function () {
  'use strict';

  // State
  let sites = [];
  let snapshots = [];
  let selectedSite = '';
  let currentMonth = new Date().getMonth();
  let currentYear = new Date().getFullYear();

  // DOM refs
  const sidebar = document.getElementById('sidebar');
  const toggleBtn = document.getElementById('toggle-sidebar');
  const siteInput = document.getElementById('site-input');
  const siteList = document.getElementById('site-list');
  const calBody = document.getElementById('cal-body');
  const calMonthYear = document.getElementById('cal-month-year');
  const calPrev = document.getElementById('cal-prev');
  const calNext = document.getElementById('cal-next');
  const themeBtn = document.getElementById('theme-toggle');
  const welcome = document.getElementById('welcome');
  const frame = document.getElementById('archive-frame');

  // -----------------------------------------------------------------------
  // Theme
  // -----------------------------------------------------------------------
  function initTheme() {
    const saved = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', saved);
    themeBtn.textContent = saved === 'dark' ? 'Light Mode' : 'Dark Mode';
  }

  function toggleTheme() {
    const cur = document.documentElement.getAttribute('data-theme');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    themeBtn.textContent = next === 'dark' ? 'Light Mode' : 'Dark Mode';
  }

  // -----------------------------------------------------------------------
  // Sidebar collapse
  // -----------------------------------------------------------------------
  toggleBtn.addEventListener('click', function () {
    sidebar.classList.toggle('collapsed');
    toggleBtn.innerHTML = sidebar.classList.contains('collapsed') ? '&rarr;' : '&larr;';
  });

  // -----------------------------------------------------------------------
  // API helpers
  // -----------------------------------------------------------------------
  async function fetchJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(r.statusText);
    return r.json();
  }

  // -----------------------------------------------------------------------
  // Site autocomplete
  // -----------------------------------------------------------------------
  async function loadSites() {
    sites = await fetchJSON('/api/sites');
  }

  function filterSites(query) {
    return sites.filter(function (s) {
      return s.toLowerCase().includes(query.toLowerCase());
    });
  }

  function renderDropdown(matches) {
    siteList.innerHTML = '';
    if (matches.length === 0 || matches.length === sites.length && siteInput.value === '') {
      siteList.classList.add('hidden');
      return;
    }
    matches.forEach(function (site) {
      const li = document.createElement('li');
      li.textContent = site;
      li.addEventListener('click', function () { selectSite(site); });
      siteList.appendChild(li);
    });
    siteList.classList.remove('hidden');
    document.querySelectorAll('.dropdown li').forEach(function (el, idx) {
      el.dataset.index = idx;
    });
  }

  function selectSite(site) {
    selectedSite = site;
    siteInput.value = site;
    siteList.classList.add('hidden');
    loadSnapshots(site);
  }

  siteInput.addEventListener('input', function () {
    const matches = filterSites(this.value);
    renderDropdown(matches);
  });

  siteInput.addEventListener('focus', function () {
    if (sites.length > 0) {
      renderDropdown(sites);
    }
  });

  siteInput.addEventListener('blur', function () {
    setTimeout(function () { siteList.classList.add('hidden'); }, 150);
  });

  siteInput.addEventListener('keydown', function (e) {
    const items = document.querySelectorAll('.dropdown li');
    const active = document.querySelector('.dropdown li.active');
    let idx = active ? parseInt(active.dataset.index) : -1;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (idx < items.length - 1) idx++;
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (idx > 0) idx--;
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (active) { selectSite(active.textContent); }
      return;
    } else {
      return;
    }
    items.forEach(function (el, i) {
      el.classList.toggle('active', i === idx);
    });
    if (items[idx]) items[idx].scrollIntoView({ block: 'nearest' });
  });

  // -----------------------------------------------------------------------
  // Snapshots / calendar
  // -----------------------------------------------------------------------
  async function loadSnapshots(site) {
    snapshots = await fetchJSON('/api/sites/' + encodeURIComponent(site) + '/snapshots');
    const today = new Date();
    if (snapshots.length > 0) {
      const lastDate = snapshots[snapshots.length - 1];
      const parts = lastDate.split('-');
      currentYear = parseInt(parts[0]);
      currentMonth = parseInt(parts[1]) - 1;
    }
    renderCalendar();
  }

  function snapshotDatesSet() {
    var set = {};
    snapshots.forEach(function (d) { set[d] = true; });
    return set;
  }

  function renderCalendar() {
    const firstDay = new Date(currentYear, currentMonth, 1).getDay();
    const daysInMonth = new Date(currentYear, currentMonth + 1, 0).getDate();
    const todayStr = new Date().toISOString().slice(0, 10);
    const snapSet = snapshotDatesSet();

    const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
      'July', 'August', 'September', 'October', 'November', 'December'];
    calMonthYear.textContent = monthNames[currentMonth] + ' ' + currentYear;

    calBody.innerHTML = '';
    let row = document.createElement('tr');
    for (let i = 0; i < firstDay; i++) {
      row.appendChild(document.createElement('td'));
    }
    for (let day = 1; day <= daysInMonth; day++) {
      const cell = document.createElement('td');
      const span = document.createElement('span');
      const dateStr = currentYear + '-' +
        String(currentMonth + 1).padStart(2, '0') + '-' +
        String(day).padStart(2, '0');

      span.textContent = day;

      if (dateStr === todayStr) {
        span.classList.add('today');
      }

      if (snapSet[dateStr]) {
        span.classList.add('clickable');
        span.addEventListener('click', function () { openSnapshot(dateStr); });
      }

      cell.appendChild(span);
      row.appendChild(cell);

      if ((firstDay + day) % 7 === 0) {
        calBody.appendChild(row);
        row = document.createElement('tr');
      }
    }
    if (row.children.length > 0) {
      calBody.appendChild(row);
    }
  }

  calPrev.addEventListener('click', function () {
    currentMonth--;
    if (currentMonth < 0) { currentMonth = 11; currentYear--; }
    renderCalendar();
  });

  calNext.addEventListener('click', function () {
    currentMonth++;
    if (currentMonth > 11) { currentMonth = 0; currentYear++; }
    renderCalendar();
  });

  // -----------------------------------------------------------------------
  // Open snapshot in viewer
  // -----------------------------------------------------------------------
  function openSnapshot(dateStr) {
    if (!selectedSite) return;
    const url = '/archive/' + encodeURIComponent(selectedSite) + '/' + dateStr + '/';
    welcome.classList.add('hidden');
    frame.classList.remove('hidden');
    frame.src = url;
  }

  // -----------------------------------------------------------------------
  // Init
  // -----------------------------------------------------------------------
  initTheme();
  themeBtn.addEventListener('click', toggleTheme);

  loadSites().then(function () {
    if (sites.length > 0) {
      selectSite(sites[0]);
    }
  });

})();
