    let token = null;
    try {
        token = sessionStorage.getItem('elara_token_mem') || localStorage.getItem('elara_token') || null;
    } catch (_) {
        token = null;
    }

    const csrfToken = (document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '').trim();
    const REFRESH_MS = 8000;
    const HEALTH_THRESHOLDS = {
        dbRateWarn: 1.0,
        dbRateBad: 3.0,
        dbContentionWarn: 5.0,
        dbContentionBad: 20.0,
        cpuWarn: 85.0,
        cpuBad: 95.0,
        memWarn: 85.0,
        memBad: 95.0,
    };
    const marketState = {
        stock_symbol: '',
        crypto_symbol: '',
        stock_timeframe: '1h',
        crypto_timeframe: '1h',
    };

    let refreshTimer = null;
    let clockTimer = null;
    let lastMarketSnapshot = { stock: null, crypto: null };

    function toNum(v, fallback = 0) {
        const n = Number(v);
        return Number.isFinite(n) ? n : fallback;
    }

    function applyMetricSeverity(el, value, warnThreshold, badThreshold) {
        if (!el) return;
        el.classList.remove('metric-ok', 'metric-warn', 'metric-bad');
        if (!Number.isFinite(value)) return;
        if (value >= badThreshold) {
            el.classList.add('metric-bad');
            return;
        }
        if (value >= warnThreshold) {
            el.classList.add('metric-warn');
            return;
        }
        el.classList.add('metric-ok');
    }

    function setMetricTooltip(el, label, value, warnThreshold, badThreshold, unitSuffix = '', digits = 2) {
        if (!el) return;
        const hasValue = Number.isFinite(value);
        const safeDigits = Math.max(0, Number(digits) || 0);
        const unit = String(unitSuffix || '');
        const valueText = hasValue ? `${value.toFixed(safeDigits)}${unit}` : 'n/a';
        const warnText = `${Number(warnThreshold).toFixed(safeDigits)}${unit}`;
        const badText = `${Number(badThreshold).toFixed(safeDigits)}${unit}`;
        let status = 'N/A';
        if (hasValue) {
            status = value >= badThreshold ? 'BAD' : (value >= warnThreshold ? 'WARN' : 'OK');
        }
        const tooltip = `${label}: ${valueText} | Status: ${status} | Warn >= ${warnText} | Bad >= ${badText}`;
        el.title = tooltip;
        el.setAttribute('aria-label', tooltip);
    }

    function getHealthLegendText() {
        return `Warn: DB>=${HEALTH_THRESHOLDS.dbRateWarn}/min, CTN>=${HEALTH_THRESHOLDS.dbContentionWarn}/min, CPU/RAM>=${HEALTH_THRESHOLDS.cpuWarn}% | Bad: DB>=${HEALTH_THRESHOLDS.dbRateBad}/min, CTN>=${HEALTH_THRESHOLDS.dbContentionBad}/min, CPU/RAM>=${HEALTH_THRESHOLDS.cpuBad}%`;
    }

    function renderHealthLegend() {
        const el = document.getElementById('health-legend-terminal');
        if (!el) return;
        el.innerText = getHealthLegendText();
    }

    function fmtPrice(v) {
        const n = Number(v);
        if (!Number.isFinite(n)) return '-';
        if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
        if (Math.abs(n) >= 1) return n.toFixed(4);
        return n.toFixed(8);
    }

    function fmtPct(v) {
        const n = Number(v);
        if (!Number.isFinite(n)) return '-';
        const sign = n > 0 ? '+' : '';
        return `${sign}${n.toFixed(2)}%`;
    }

    function fmtTs(v) {
        const n = Number(v);
        if (!Number.isFinite(n) || n <= 0) return '-';
        return new Date(n * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function setPill(mode) {
        const pill = document.getElementById('api-status-pill');
        if (!pill) return;
        const next = String(mode || '').toLowerCase();
        pill.className = 'api-pill';
        if (next === 'ok') {
            pill.classList.add('api-ok');
            pill.innerText = 'API LIVE';
            return;
        }
        if (next === 'warn') {
            pill.classList.add('api-warn');
            pill.innerText = 'API DEGRADED';
            return;
        }
        if (next === 'err') {
            pill.classList.add('api-err');
            pill.innerText = 'API ERROR';
            return;
        }
        pill.classList.add('api-init');
        pill.innerText = 'API INIT';
    }

    function authHeaders(extra = {}) {
        return {
            ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
            ...(csrfToken ? { 'X-CSRFToken': csrfToken, 'X-CSRF-Token': csrfToken } : {}),
            ...extra,
        };
    }

    async function fetchJson(url, options = {}, redirectOn401 = true) {
        try {
            const out = await fetch(url, {
                method: options.method || 'GET',
                headers: authHeaders(options.headers || {}),
                body: options.body,
                credentials: 'same-origin',
            });
            let data = {};
            try { data = await out.json(); } catch (_) { data = {}; }
            if ((out.status === 401 || out.status === 422) && redirectOn401) {
                token = null;
                try { sessionStorage.removeItem('elara_token_mem'); } catch (_) {}
                try { localStorage.removeItem('elara_token'); } catch (_) {}
                window.location.href = '/login';
                return { ok: false, status: out.status, data };
            }
            return { ok: out.ok, status: out.status, data };
        } catch (e) {
            return { ok: false, status: 0, data: { message: String(e || 'network_error') } };
        }
    }

    function syncSymbolSelect(selectId, availableRows, selectedSymbol) {
        const el = document.getElementById(selectId);
        if (!el) return '';
        const rows = Array.isArray(availableRows) ? availableRows : [];
        const selected = String(selectedSymbol || '').trim().toUpperCase();
        const pool = [];
        rows.forEach((row) => {
            const sym = String((row && row.symbol) || '').trim().toUpperCase();
            if (sym && !pool.includes(sym)) pool.push(sym);
        });
        if (selected && !pool.includes(selected)) pool.unshift(selected);

        if (!pool.length) {
            el.innerHTML = '<option value="">-</option>';
            el.value = '';
            return '';
        }

        const current = String(el.value || '').trim().toUpperCase();
        el.innerHTML = '';
        pool.forEach((sym) => {
            const opt = document.createElement('option');
            opt.value = sym;
            opt.innerText = sym;
            el.appendChild(opt);
        });

        const final = current && pool.includes(current)
            ? current
            : (selected && pool.includes(selected) ? selected : pool[0]);
        el.value = final;
        return final;
    }

    function drawLine(canvasId, points, lineColor) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const width = Math.max(220, canvas.clientWidth || 220);
        const height = Math.max(120, canvas.clientHeight || 120);
        const ratio = window.devicePixelRatio || 1;
        const renderWidth = Math.floor(width * ratio);
        const renderHeight = Math.floor(height * ratio);

        if (canvas.width !== renderWidth || canvas.height !== renderHeight) {
            canvas.width = renderWidth;
            canvas.height = renderHeight;
        }
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, width, height);

        const rows = (Array.isArray(points) ? points : [])
            .map((row) => Number((row && row.price)))
            .filter((v) => Number.isFinite(v));
        if (rows.length < 2) {
            ctx.strokeStyle = 'rgba(159, 179, 206, 0.45)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(0, height * 0.5);
            ctx.lineTo(width, height * 0.5);
            ctx.stroke();
            return;
        }

        const min = Math.min(...rows);
        const max = Math.max(...rows);
        const span = Math.max(1e-9, max - min);
        const px = (idx) => (idx / (rows.length - 1)) * width;
        const py = (price) => height - (((price - min) / span) * (height - 16) + 8);

        const grad = ctx.createLinearGradient(0, 0, 0, height);
        grad.addColorStop(0, lineColor);
        grad.addColorStop(1, 'rgba(0,0,0,0)');

        ctx.beginPath();
        ctx.moveTo(px(0), py(rows[0]));
        for (let i = 1; i < rows.length; i += 1) {
            ctx.lineTo(px(i), py(rows[i]));
        }
        ctx.strokeStyle = lineColor;
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.lineTo(width, height);
        ctx.lineTo(0, height);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();
    }

    function renderMarketNode(prefix, payload) {
        const data = payload || {};
        const symbol = String(data.symbol || '-');
        const price = fmtPrice(data.price);
        const change = fmtPct(data.change_pct);
        const changeNum = Number(data.change_pct);
        const updated = fmtTs(data.updated_at);

        const symbolEl = document.getElementById(`${prefix}-symbol`);
        const priceEl = document.getElementById(`${prefix}-price`);
        const changeEl = document.getElementById(`${prefix}-change`);
        const updatedEl = document.getElementById(`${prefix}-updated`);

        if (symbolEl) symbolEl.innerText = symbol;
        if (priceEl) priceEl.innerText = price;
        if (updatedEl) updatedEl.innerText = updated;
        if (changeEl) {
            changeEl.innerText = change;
            changeEl.classList.remove('text-up', 'text-down');
            if (Number.isFinite(changeNum) && changeNum > 0) changeEl.classList.add('text-up');
            if (Number.isFinite(changeNum) && changeNum < 0) changeEl.classList.add('text-down');
        }
    }

    function renderTape(stockRows, cryptoRows) {
        const tbody = document.getElementById('tape-body');
        if (!tbody) return;
        tbody.innerHTML = '';

        const pack = [];
        (Array.isArray(stockRows) ? stockRows : []).forEach((row) => pack.push({ type: 'STOCK', ...row }));
        (Array.isArray(cryptoRows) ? cryptoRows : []).forEach((row) => pack.push({ type: 'CRYPTO', ...row }));
        const rows = pack.slice(0, 16);

        if (!rows.length) {
            const tr = document.createElement('tr');
            tr.innerHTML = '<td colspan="4" class="muted-cell">No market tape available.</td>';
            tbody.appendChild(tr);
            return;
        }

        rows.forEach((row) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${String(row.type || '-')}</td>
                <td>${String(row.symbol || '-')}</td>
                <td class="mono-text">${fmtPrice(row.price)}</td>
                <td>${fmtTs(row.updated_at)}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    function renderLeaderList(targetId, rows) {
        const target = document.getElementById(targetId);
        if (!target) return;
        target.innerHTML = '';
        const items = Array.isArray(rows) ? rows.slice(0, 8) : [];
        if (!items.length) {
            const li = document.createElement('li');
            li.className = 'muted-cell';
            li.innerText = 'No qualified setup.';
            target.appendChild(li);
            return;
        }
        items.forEach((row) => {
            const symbol = String((row && row.symbol) || '-');
            const score = toNum((row && row.score), 0).toFixed(1);
            const rr = toNum((row && row.rr), 0).toFixed(2);
            const setup = String((row && row.setup) || '-');
            const li = document.createElement('li');
            li.innerHTML = `<strong>${symbol}</strong><span>Score ${score}</span><span>RR ${rr}</span><span>${setup}</span>`;
            target.appendChild(li);
        });
    }

    function renderDecisions(rows) {
        const tbody = document.getElementById('decision-body');
        if (!tbody) return;
        tbody.innerHTML = '';
        const items = Array.isArray(rows) ? rows.slice(0, 20) : [];
        if (!items.length) {
            const tr = document.createElement('tr');
            tr.innerHTML = '<td colspan="5" class="muted-cell">No recent decisions.</td>';
            tbody.appendChild(tr);
            return;
        }
        items.forEach((row) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${String((row && row.entry_time) || '-')}</td>
                <td>${String((row && row.symbol) || '-')}</td>
                <td>${String((row && row.direction) || '-')}</td>
                <td>${String((row && row.status) || '-')}</td>
                <td>${String((row && row.entry_reason) || '-')}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    function renderHealth(statusData) {
        const payload = statusData || {};
        const health = payload.health || {};
        const metrics = payload.trading_metrics || {};
        const skill = payload.skill_analytics || {};
        const tradingBot = payload.trading_bot || {};
        const cpuPct = health.cpu_pct == null ? Number.NaN : toNum(health.cpu_pct, Number.NaN);
        const memPct = health.mem_pct == null ? Number.NaN : toNum(health.mem_pct, Number.NaN);
        const dbRatePerMin = toNum(health.db_lock_rate_per_min, 0);
        const dbContentionRatePerMin = toNum(health.db_write_lock_contention_rate_per_min, 0);
        const cpuEl = document.getElementById('metric-cpu');
        const memEl = document.getElementById('metric-mem');
        const dbRateEl = document.getElementById('metric-dblocks');
        const dbContentionEl = document.getElementById('metric-db-contention');

        if (cpuEl) cpuEl.innerText = Number.isFinite(cpuPct) ? `${cpuPct.toFixed(1)}%` : 'n/a';
        if (memEl) memEl.innerText = Number.isFinite(memPct) ? `${memPct.toFixed(1)}%` : 'n/a';
        applyMetricSeverity(cpuEl, cpuPct, HEALTH_THRESHOLDS.cpuWarn, HEALTH_THRESHOLDS.cpuBad);
        applyMetricSeverity(memEl, memPct, HEALTH_THRESHOLDS.memWarn, HEALTH_THRESHOLDS.memBad);
        setMetricTooltip(cpuEl, 'CPU', cpuPct, HEALTH_THRESHOLDS.cpuWarn, HEALTH_THRESHOLDS.cpuBad, '%', 1);
        setMetricTooltip(memEl, 'RAM', memPct, HEALTH_THRESHOLDS.memWarn, HEALTH_THRESHOLDS.memBad, '%', 1);
        if (dbRateEl) dbRateEl.innerText = dbRatePerMin.toFixed(2);
        if (dbContentionEl) dbContentionEl.innerText = dbContentionRatePerMin.toFixed(2);
        applyMetricSeverity(dbRateEl, dbRatePerMin, HEALTH_THRESHOLDS.dbRateWarn, HEALTH_THRESHOLDS.dbRateBad);
        applyMetricSeverity(
            dbContentionEl,
            dbContentionRatePerMin,
            HEALTH_THRESHOLDS.dbContentionWarn,
            HEALTH_THRESHOLDS.dbContentionBad
        );
        setMetricTooltip(dbRateEl, 'DB Locks', dbRatePerMin, HEALTH_THRESHOLDS.dbRateWarn, HEALTH_THRESHOLDS.dbRateBad, '/min', 2);
        setMetricTooltip(
            dbContentionEl,
            'DB Contention',
            dbContentionRatePerMin,
            HEALTH_THRESHOLDS.dbContentionWarn,
            HEALTH_THRESHOLDS.dbContentionBad,
            '/min',
            2
        );
        document.getElementById('metric-equity').innerText = `$${toNum(metrics.equity, 0).toFixed(2)}`;
        document.getElementById('metric-winrate').innerText = `${(toNum(metrics.win_rate, 0) * 100).toFixed(2)}%`;
        document.getElementById('metric-trades').innerText = `${Math.trunc(toNum(metrics.trade_count, 0))}`;
        document.getElementById('metric-skill-profile').innerText = String(skill.profile || '-');
        document.getElementById('metric-skill-hitrate').innerText = `${toNum(skill.route_hit_rate_pct, 0).toFixed(2)}%`;
        document.getElementById('metric-skill-blockrate').innerText = `${toNum(skill.policy_block_rate_pct, 0).toFixed(2)}%`;

        document.getElementById('metric-client-ip').innerText = `Client IP: ${String(payload.client_ip || '-')}`;
        document.getElementById('metric-server-ip').innerText = `Server IP: ${String(payload.server_ip || '-')}`;
        const botRunning = tradingBot && (tradingBot.is_running === true || tradingBot.running === true);
        document.getElementById('metric-bot-state').innerText = `Bot: ${botRunning ? 'RUNNING' : 'IDLE'}`;
    }

    function renderSelfOps(out) {
        const note = document.getElementById('selfops-note');
        const jsonEl = document.getElementById('selfops-json');

        if (!out) return;
        if (out.status === 401 || out.status === 403) {
            note.innerText = 'Admin role required.';
            document.getElementById('selfops-running').innerText = 'LOCKED';
            document.getElementById('selfops-healthy').innerText = '-';
            document.getElementById('selfops-deploy').innerText = '-';
            document.getElementById('selfops-healnow').innerText = '-';
            if (jsonEl) jsonEl.innerText = '{"message":"admin_only"}';
            return;
        }
        if (!out.ok) {
            note.innerText = 'Self-ops feed unavailable.';
            document.getElementById('selfops-running').innerText = 'ERROR';
            document.getElementById('selfops-healthy').innerText = '-';
            document.getElementById('selfops-deploy').innerText = '-';
            document.getElementById('selfops-healnow').innerText = '-';
            if (jsonEl) jsonEl.innerText = JSON.stringify(out.data || {}, null, 2);
            return;
        }

        const data = out.data || {};
        document.getElementById('selfops-running').innerText = data.running ? 'RUNNING' : 'STOPPED';
        document.getElementById('selfops-healthy').innerText = data.healthy ? 'OK' : 'DEGRADED';
        document.getElementById('selfops-deploy').innerText = data.deploy_running ? 'ACTIVE' : 'IDLE';
        document.getElementById('selfops-healnow').innerText = data.heal_now_running ? 'ACTIVE' : 'IDLE';
        note.innerText = data.last_error ? `Last error: ${String(data.last_error)}` : 'Self-ops telemetry live.';

        const view = {
            interval_sec: data.interval_sec,
            last_heal_report: data.last_heal_report || {},
            last_deploy_report: data.last_deploy_report || {},
        };
        if (jsonEl) jsonEl.innerText = JSON.stringify(view, null, 2);
    }

    async function loadMarket(force = false) {
        const q = new URLSearchParams();
        q.set('limit', '120');
        q.set('stock_timeframe', marketState.stock_timeframe || '1h');
        q.set('crypto_timeframe', marketState.crypto_timeframe || '1h');
        if (marketState.stock_symbol) q.set('stock_symbol', marketState.stock_symbol);
        if (marketState.crypto_symbol) q.set('crypto_symbol', marketState.crypto_symbol);
        if (force) q.set('refresh', '1');

        const out = await fetchJson(`/api/market/realtime?${q.toString()}`);
        const note = document.getElementById('market-note');
        if (!out.ok) {
            if (note) note.innerText = (out.data && out.data.message) ? String(out.data.message) : 'Market feed unavailable.';
            return false;
        }

        const market = out.data || {};
        const stock = market.stock || {};
        const crypto = market.crypto || {};
        lastMarketSnapshot = { stock, crypto };

        const finalStockSymbol = syncSymbolSelect('sel-stock-symbol', stock.available_symbols, stock.symbol);
        const finalCryptoSymbol = syncSymbolSelect('sel-crypto-symbol', crypto.available_symbols, crypto.symbol);
        if (finalStockSymbol) marketState.stock_symbol = finalStockSymbol;
        if (finalCryptoSymbol) marketState.crypto_symbol = finalCryptoSymbol;

        renderMarketNode('stock', stock);
        renderMarketNode('crypto', crypto);
        renderTape(stock.available_symbols, crypto.available_symbols);

        const stockUp = Number(stock.change_pct);
        const cryptoUp = Number(crypto.change_pct);
        drawLine('stock-canvas', stock.points, Number.isFinite(stockUp) && stockUp < 0 ? '#ff6e87' : '#46dba9');
        drawLine('crypto-canvas', crypto.points, Number.isFinite(cryptoUp) && cryptoUp < 0 ? '#ff6e87' : '#4ec9ff');

        if (note) note.innerText = `Updated ${new Date().toLocaleTimeString()}`;
        return true;
    }

    async function loadMissionControl() {
        const out = await fetchJson('/api/mission-control');
        const note = document.getElementById('screener-note');
        if (!out.ok) {
            if (note) note.innerText = 'Mission feed unavailable.';
            renderLeaderList('stock-leaders', []);
            renderLeaderList('crypto-leaders', []);
            return false;
        }
        const data = out.data || {};
        const stockRows = (data.stock && data.stock.top) || [];
        const cryptoRows = (data.crypto && data.crypto.top) || [];
        renderLeaderList('stock-leaders', stockRows);
        renderLeaderList('crypto-leaders', cryptoRows);
        if (note) note.innerText = `Updated ${new Date().toLocaleTimeString()}`;
        return true;
    }

    async function loadSystemStatus() {
        const out = await fetchJson('/api/status');
        const note = document.getElementById('health-note');
        if (!out.ok) {
            if (note) note.innerText = 'Status feed unavailable.';
            return false;
        }
        renderHealth(out.data || {});
        if (note) note.innerText = `Updated ${new Date().toLocaleTimeString()}`;
        return true;
    }

    async function loadDecisions() {
        const out = await fetchJson('/api/trading/decisions?limit=20');
        const note = document.getElementById('decision-note');
        if (!out.ok) {
            if (note) note.innerText = 'Decision feed unavailable.';
            renderDecisions([]);
            return false;
        }
        const rows = (out.data && out.data.items) || [];
        renderDecisions(rows);
        if (note) note.innerText = `Rows: ${rows.length}`;
        return true;
    }

    async function loadSelfOps() {
        const out = await fetchJson('/api/selfops/status', {}, false);
        renderSelfOps(out);
        if (out.status === 401 || out.status === 403) return null;
        return out.ok;
    }

    function tickClock() {
        const el = document.getElementById('clock-utc');
        if (!el) return;
        const now = new Date();
        const utc = now.toISOString().slice(11, 19);
        const local = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        el.innerText = `UTC ${utc} | Local ${local}`;
    }

    async function refreshAll(force = false) {
        const results = await Promise.all([
            loadMarket(force),
            loadMissionControl(),
            loadSystemStatus(),
            loadDecisions(),
            loadSelfOps(),
        ]);
        const required = results.slice(0, 4);
        const okCount = required.filter((v) => v === true).length;
        if (okCount === required.length) {
            setPill('ok');
            return;
        }
        if (okCount >= 2) {
            setPill('warn');
            return;
        }
        setPill('err');
    }

    async function logout() {
        try {
            await fetchJson('/api/logout', { method: 'POST' }, false);
        } catch (_) {}
        token = null;
        try { sessionStorage.removeItem('elara_token_mem'); } catch (_) {}
        try { localStorage.removeItem('elara_token'); } catch (_) {}
        window.location.href = '/login';
    }

    document.getElementById('btn-refresh').addEventListener('click', async () => {
        setPill('init');
        await refreshAll(true);
    });
    document.getElementById('btn-portal').addEventListener('click', () => { window.location.href = '/portal'; });
    document.getElementById('btn-legacy').addEventListener('click', () => { window.location.href = '/dashboard'; });
    document.getElementById('btn-account').addEventListener('click', () => { window.location.href = '/account'; });
    document.getElementById('btn-logout').addEventListener('click', logout);

    document.getElementById('sel-stock-symbol').addEventListener('change', async (event) => {
        marketState.stock_symbol = String(event.target.value || '').trim().toUpperCase();
        await loadMarket(false);
    });
    document.getElementById('sel-crypto-symbol').addEventListener('change', async (event) => {
        marketState.crypto_symbol = String(event.target.value || '').trim().toUpperCase();
        await loadMarket(false);
    });
    document.getElementById('sel-stock-timeframe').addEventListener('change', async (event) => {
        marketState.stock_timeframe = String(event.target.value || '1h').trim().toLowerCase();
        await loadMarket(false);
    });
    document.getElementById('sel-crypto-timeframe').addEventListener('change', async (event) => {
        marketState.crypto_timeframe = String(event.target.value || '1h').trim().toLowerCase();
        await loadMarket(false);
    });

    window.addEventListener('resize', () => {
        const stock = lastMarketSnapshot.stock || {};
        const crypto = lastMarketSnapshot.crypto || {};
        const stockUp = Number(stock.change_pct);
        const cryptoUp = Number(crypto.change_pct);
        drawLine('stock-canvas', stock.points, Number.isFinite(stockUp) && stockUp < 0 ? '#ff6e87' : '#46dba9');
        drawLine('crypto-canvas', crypto.points, Number.isFinite(cryptoUp) && cryptoUp < 0 ? '#ff6e87' : '#4ec9ff');
    });

    (async function initTerminal() {
        tickClock();
        clockTimer = setInterval(tickClock, 1000);
        renderHealthLegend();
        setPill('init');
        await refreshAll(true);
        refreshTimer = setInterval(() => {
            refreshAll(false);
        }, REFRESH_MS);
    })();
