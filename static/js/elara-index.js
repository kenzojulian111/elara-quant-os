    let token = null;
    try {
        token = sessionStorage.getItem('elara_token_mem') || null;
    } catch (_) {
        token = null;
    }
    const csrfToken = (document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '').trim();
    const guestAllowed = Boolean(window.__ELARA_GUEST_ALLOWED__);
    let chartUpdateTimer = null;
    let lastStatusTs = null;
    const setToken = (t) => {
        token = t;
        try {
            if (t) sessionStorage.setItem('elara_token_mem', t);
            else sessionStorage.removeItem('elara_token_mem');
        } catch (_) {}
        if (socket) {
            socket.auth = token ? { token } : {};
            socket.disconnect().connect();
        }
    };
    const authHeaders = (base = {}) => {
        const headers = { ...base };
        if (token) headers['Authorization'] = `Bearer ${token}`;
        if (csrfToken) {
            headers['X-CSRFToken'] = csrfToken;
            headers['X-CSRF-Token'] = csrfToken;
        }
        return headers;
    };

    // Intro overlay control (only show once per session)
    const introOverlay = document.getElementById('intro-overlay');
    const introShown = sessionStorage.getItem('intro_shown') === '1';

    // Connect to Backend Socket (auth via cookie/header payload; no query token)
    const socket = io({
        auth: token ? { token } : {},
        reconnectionAttempts: 5,
        timeout: 10000,
        transports: ["polling"]  // force polling to avoid WS handshake loops
    });
    let socketConnected = false;
    let statusPoller = null;

    // Chart Variables
    let currentChartMode = 'power';
    let chartInstance = null;
    let screenerScoreChart = null;
    const commandHistory = [];
    let commandHistoryCursor = 0;
    const chartDataBuffers = {
        power: Array(20).fill(0),
        pnl: Array(20).fill(0),
        equity: Array(20).fill(0),
        wr: Array(20).fill(0)
    };

    const chartConfigs = {
        power: { label: 'Power (W)', color: '#57c7ff', gradient: ['rgba(87, 199, 255, 0.35)', 'rgba(87, 199, 255, 0)'] },
        pnl:   { label: 'PnL ($)',   color: '#43e5b0', gradient: ['rgba(67, 229, 176, 0.3)', 'rgba(67, 229, 176, 0)'] },
        equity:{ label: 'Equity ($)',color: '#f5b14d', gradient: ['rgba(245, 177, 77, 0.3)', 'rgba(245, 177, 77, 0)'] },
        wr:    { label: 'WinRate',   color: '#9eb5d0', gradient: ['rgba(158, 181, 208, 0.25)', 'rgba(158, 181, 208, 0)'] }
    };

    document.addEventListener("DOMContentLoaded", () => {
        initChart();
        setupInputListener();
        initKeyboardShortcuts();
        initChatAutoFollow();
        initQuickActions();
        startStatusPolling();
        if (!guestAllowed) startTokenRefresh();
        fetchScreener();
        fetchCryptoScreener();
        setInterval(fetchCryptoScreener, 30000);

        if (!introShown) {
            runIntroTyping();
            setTimeout(() => {
                if (introOverlay) introOverlay.classList.add('intro-hidden');
                sessionStorage.setItem('intro_shown', '1');
            }, 2200);
        } else if (introOverlay) {
            introOverlay.classList.add('intro-hidden');
        }
        
        // Date Widget (Client Side)
        const d = new Date();
        const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
        document.getElementById('widget-date').innerText = `${months[d.getMonth()]} ${d.getDate()}`;
    });

    // --- VIEW SWITCHING LOGIC (SPA) ---
    function openPortfolio() {
        switchView('portfolio');
    }

    function openTerminal() {
        window.location.href = '/terminal';
    }

    function switchView(viewName) {
        const btnDash = document.getElementById('btn-dash');
        const btnPort = document.getElementById('btn-port');
        
        document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active-view'));
        
        if (viewName === 'portfolio') {
            document.getElementById('portfolio-view').classList.add('active-view');
            btnDash.classList.remove('active');
            btnPort.classList.add('active');
        } else {
            document.getElementById('dashboard-view').classList.add('active-view');
            btnPort.classList.remove('active');
            btnDash.classList.add('active');
        }
    }

    function startStatusPolling() {
        if (statusPoller) return;
        statusPoller = setInterval(fetchStatus, 3000);
        fetchStatus();
    }

    function decodeExp(jwt) {
        try {
            const payload = JSON.parse(atob(jwt.split('.')[1]));
            return payload.exp ? payload.exp * 1000 : null;
        } catch (e) { return null; }
    }

    function startTokenRefresh() {
        const schedule = () => {
            const expMs = decodeExp(token);
            const now = Date.now();
            const leadMs = 120_000; // refresh 2 menit sebelum expired
            let delay = expMs ? Math.max(60_000, expMs - now - leadMs) : 600_000;
            setTimeout(refreshToken, delay);
        };
        async function refreshToken() {
            try {
                const res = await fetch('/api/refresh', {
                    method: 'POST',
                    headers: authHeaders({ 'Content-Type': 'application/json' })
                });
                if (res.ok) {
                    const data = await res.json();
                    if (data.access_token) setToken(data.access_token);
                    schedule();
                } else if (res.status === 401) {
                    setToken(null);
                    try { localStorage.removeItem('elara_token'); } catch (_) {}
                    window.location.href = '/login';
                } else {
                    console.warn('Token refresh status', res.status);
                    schedule();
                }
            } catch (e) {
                console.warn('Token refresh gagal', e);
                schedule();
            }
        }
        schedule();
    }

    async function fetchStatus() {
        try {
            const res = await fetch('/api/status', { headers: authHeaders() });
            if (res.status === 401) {
                setToken(null);
                try { localStorage.removeItem('elara_token'); } catch (_) {}
                window.location.href = '/login';
                return;
            }
            if (!res.ok) {
                addLog(`Status error: ${res.status}`);
                return;
            }
            const data = await res.json();
            applyStatusData(data);
            updateStatus(true, "ONLINE");
        } catch (e) {
            addLog("Status fetch failed");
            console.error(e);
        }
    }

    const toNum = (val, def = 0) => {
        const n = Number(val);
        return Number.isFinite(n) ? n : def;
    };

    function applyStatusData(data) {
        if (!data) return;
        if (data.arus !== undefined) {
            const arus = toNum(data.arus, 0);
            const power = Math.round(arus * 220);

            const powerTxt = power > 0 ? power : '--';
            const ampTxt = arus > 0 ? arus.toFixed(2) : '--';
            const kwhTxt = data.kwhToday ? toNum(data.kwhToday, 0).toFixed(2) : '--';
            const costTxt = data.rupiahToday ? toNum(data.rupiahToday, 0).toLocaleString('id-ID') : '--';
            const tempTxt = (data.suhu !== undefined && data.suhu !== null) ? toNum(data.suhu, 0).toFixed(1) : '--';

            document.getElementById('val-power').innerText = powerTxt;
            document.getElementById('val-amp').innerText = ampTxt;
            document.getElementById('val-kwh').innerText = kwhTxt;
            document.getElementById('val-cost').innerText = costTxt;
            document.getElementById('val-temp').innerText = tempTxt;
            
            updateBuffer('power', power);
        }

        if (data.trading_metrics) {
            const pnl = toNum(data.trading_metrics.total_net_pnl, 0);
            const wr = toNum(data.trading_metrics.win_rate, 0) * 100;
            const equity = toNum(data.trading_metrics.equity, null);
            const cash = toNum(data.trading_metrics.cash, null);
            
            document.getElementById('val-pnl').innerText = pnl.toFixed(2);
            document.getElementById('val-wr-widget').innerText = wr.toFixed(0) + "%";
            if (equity !== null) {
                document.getElementById('val-equity').innerText = `$${equity.toFixed(2)}`;
                document.getElementById('portfolio-equity').innerText = `$ ${equity.toFixed(2)}`;
            }
            if (cash !== null) {
                document.getElementById('val-cash').innerText = `$${cash.toFixed(2)}`;
                document.getElementById('portfolio-cash').innerText = `$ ${cash.toFixed(2)}`;
            }
            document.getElementById('portfolio-pnl').innerText = `$ ${pnl.toFixed(2)}`;
            
            if (Array.isArray(data.trading_metrics.pnl_history) && data.trading_metrics.pnl_history.length) {
                updateSeries('pnl', data.trading_metrics.pnl_history);
            } else {
                updateBuffer('pnl', pnl);
            }
            if (Array.isArray(data.trading_metrics.equity_history) && data.trading_metrics.equity_history.length) {
                const series = data.trading_metrics.equity_history.map(e => e.equity ?? 0);
                updateSeries('equity', series);
            }
            updateBuffer('wr', wr);
        }

        scheduleChartUpdate();
        if (data.devices) renderDevices(data.devices);
        if (data.logs) addLogBatch(data.logs);
        if (data.health) updateHealth(data.health);
        if (data.log_flood) showLogFlood(true); else showLogFlood(false);
        if (data.presence) updatePresence(data.presence);
        if (data.trading_bot) updateTradingBot(data.trading_bot);
        if (data.client_ip || data.server_ip) {
            updateIP(data.client_ip, data.server_ip);
        }
        lastStatusTs = Date.now();
        updateUpdatedAt();
    }

    // --- SOCKET EVENTS ---
    socket.on('connect', () => {
        socketConnected = true;
        updateStatus(true, "ONLINE");
        addLog("Server connected.");
        document.getElementById('lbl-amp-status').innerText = "Realtime";
        fetchStatus();
    });

    socket.on('disconnect', () => {
        socketConnected = false;
        updateStatus(false, "OFFLINE");
        addLog("Connection lost.");
        setOfflineState();
        startStatusPolling();
    });

    socket.on('connect_error', () => {
        socketConnected = false;
        updateStatus(false, "OFFLINE");
        startStatusPolling();
    });

    function updateStatus(isOnline, text) {
        const badge = document.getElementById('system-status');
        const span = document.getElementById('status-text');
        
        if (isOnline) {
            badge.className = 'status-badge online';
            span.innerText = text || "ONLINE";
        } else {
            badge.className = 'status-badge offline';
            span.innerText = text || "OFFLINE";
        }
    }

    function updatePresence(state) {
        const badge = document.getElementById('presence-badge');
        const span = document.getElementById('presence-text');
        const val = (state || 'unknown').toUpperCase();
        span.innerText = `PRESENCE ${val}`;
        if (val === 'HOME') {
            badge.className = 'status-badge online';
        } else if (val === 'AWAY') {
            badge.className = 'status-badge offline';
        } else {
            badge.className = 'status-badge';
        }
    }

    function setOfflineState() {
        // Zero out data when disconnected
        document.getElementById('val-power').innerText = "0";
        document.getElementById('val-amp').innerText = "0.00";
        document.getElementById('lbl-amp-status').innerText = "Offline"; 
        
        document.getElementById('val-cost').innerText = "0";
        document.getElementById('val-kwh').innerText = "0.00";
        document.getElementById('val-temp').innerText = "0.0";
        
        document.getElementById('val-pnl').innerText = "0.00";
        document.getElementById('val-wr-widget').innerText = "0%";
        document.getElementById('val-equity').innerText = "$0.00";
        document.getElementById('val-cash').innerText = "$0.00";
        document.getElementById('portfolio-equity').innerText = "$ 0.00";
        document.getElementById('portfolio-cash').innerText = "$ 0.00";
        document.getElementById('portfolio-pnl').innerText = "$ 0.00";
        document.getElementById('health-db-locks').innerText = "0";
        document.getElementById('health-db-rate').innerText = "0/min";
        document.getElementById('health-loop-lag').innerText = "0ms";
        document.getElementById('health-fw-queue').innerText = "0";
        document.getElementById('health-cpu').innerText = "0%";
        document.getElementById('health-mem').innerText = "0%";
        document.getElementById('health-badge').style.display = 'none';
        updatePresence("unknown");
        document.getElementById('health-cpu').innerText = "0%";
        document.getElementById('health-mem').innerText = "0%";
        
        document.getElementById('device-container').innerHTML = 
            `<div class="dev-item dev-item-disconnected">
                <i class="fa-solid fa-power-off dev-offline-icon"></i>
                <div class="dev-name">DISCONNECTED</div>
            </div>`;

        document.getElementById('sp-track').innerText = "Not Connected";
        document.getElementById('sp-artist').innerText = "Offline";
        screenerStatus.innerText = "Offline";
        screenerList.innerHTML = `<div class="log-item">Disconnected</div>`;
        if (cryptoScreenerStatus) cryptoScreenerStatus.innerText = "Offline";
        if (cryptoScreenerList) cryptoScreenerList.innerHTML = `<div class="log-item">Disconnected</div>`;
    }

    // --- CHART LOGIC ---
    function initChart() {
        const ctx = document.getElementById('mainChart').getContext('2d');
        const config = chartConfigs[currentChartMode];
        const gradient = ctx.createLinearGradient(0, 0, 0, 300);
        gradient.addColorStop(0, config.gradient[0]);
        gradient.addColorStop(1, config.gradient[1]);

        chartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: Array(20).fill(''),
                datasets: [{
                    label: config.label,
                    data: chartDataBuffers[currentChartMode],
                    borderColor: config.color,
                    backgroundColor: gradient,
                    borderWidth: 2,
                    tension: 0.4,
                    fill: true,
                    pointRadius: 0,
                    pointHoverRadius: 5,
                    pointBackgroundColor: '#000',
                    pointBorderColor: config.color,
                    pointBorderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: { display: false }, tooltip: {
                    backgroundColor: '#111', titleColor: '#888', bodyColor: '#fff', borderColor: '#333', borderWidth: 1, padding: 10
                }},
                scales: {
                    x: { display: false },
                    y: { 
                        grid: { color: '#111', drawBorder: false }, 
                        ticks: { color: '#444', font: {family: 'JetBrains Mono', size: 10} },
                        beginAtZero: true 
                    }
                },
                interaction: {
                    mode: 'nearest',
                    intersect: false
                }
            }
        });
    }

    window.switchChart = function(mode) {
        currentChartMode = mode;
        const config = chartConfigs[mode];
        
        document.querySelectorAll('.chart-btn').forEach(b => {
            b.classList.remove('active');
            if(b.dataset.mode === mode) b.classList.add('active');
        });

        const ctx = chartInstance.ctx;
        const gradient = ctx.createLinearGradient(0, 0, 0, 300);
        gradient.addColorStop(0, config.gradient[0]);
        gradient.addColorStop(1, config.gradient[1]);

        chartInstance.data.datasets[0].label = config.label;
        chartInstance.data.datasets[0].borderColor = config.color;
        chartInstance.data.datasets[0].pointBorderColor = config.color;
        chartInstance.data.datasets[0].backgroundColor = gradient;
        chartInstance.data.datasets[0].data = chartDataBuffers[mode];
        chartInstance.update();
    };

    // --- SOCKET DATA HANDLING ---
    socket.on('status_update', (data) => {
        applyStatusData(data);
    });

    socket.on('command_response', (data) => {
        const msg = String((data && data.message) || '').trim() || 'Command selesai, tapi belum ada output.';
        const history = document.getElementById('ai-response');
        history.innerHTML += `<div class="msg-ai">${msg}</div>`;
        history.scrollTop = history.scrollHeight;

        // Parse music info if available in response
        if (msg.includes("Memutar:")) {
            const parts = msg.replace(/.*Memutar:/, "").split("oleh");
            if(parts.length >= 1) document.getElementById('sp-track').innerText = parts[0].trim();
            if(parts.length >= 2) document.getElementById('sp-artist').innerText = parts[1].trim();
        }
    });

    function initKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            const key = String(e.key || '').toLowerCase();
            if ((e.ctrlKey || e.metaKey) && key === 'k') {
                e.preventDefault();
                const input = document.getElementById('cmd-input');
                if (input) {
                    input.focus();
                    input.select();
                }
                return;
            }
            if (e.key === 'Escape') {
                document.body.classList.remove('sidebar-open');
                toggleHealthDrawer(false);
                closeScreenerPanel();
            }
        });
    }

    function initChatAutoFollow() {
        const history = document.getElementById('ai-response');
        if (!history) return;
        let stickToBottom = true;
        history.addEventListener('scroll', () => {
            const dist = history.scrollHeight - history.scrollTop - history.clientHeight;
            stickToBottom = dist < 28;
        });
        const observer = new MutationObserver(() => {
            if (stickToBottom) history.scrollTop = history.scrollHeight;
        });
        observer.observe(history, { childList: true, subtree: false });
    }

    function initQuickActions() {
        const updatedPill = document.getElementById('updated-pill');
        if (updatedPill) {
            updatedPill.title = 'Double click to refresh status';
            updatedPill.addEventListener('dblclick', () => {
                fetchStatus();
            });
        }

        document.querySelectorAll('[role="button"][tabindex="0"]').forEach((el) => {
            el.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    el.click();
                }
            });
        });
    }

    // Sidebar toggle (mobile)
    window.toggleSidebar = function() {
        document.body.classList.toggle('sidebar-open');
    };
    // Auto-close sidebar when main area touched on mobile
    document.addEventListener('click', (e) => {
        if (window.innerWidth > 900) return;
        const sidebar = document.querySelector('.sidebar');
        const burger = document.querySelector('.burger-btn');
        if (!sidebar.contains(e.target) && !burger.contains(e.target)) {
            document.body.classList.remove('sidebar-open');
        }
    }, true);

    // --- HELPER FUNCTIONS ---
    function updateBuffer(mode, val) {
        chartDataBuffers[mode].shift();
        chartDataBuffers[mode].push(val);
        if (currentChartMode === mode && chartInstance) {
            chartInstance.data.datasets[0].data = chartDataBuffers[mode];
        }
    }

    function updateSeries(mode, series) {
        if (!Array.isArray(series)) return;
        const trimmed = series.slice(-20);
        const pad = 20 - trimmed.length;
        chartDataBuffers[mode] = [
            ...Array(Math.max(0, pad)).fill(0),
            ...trimmed
        ];
        if (currentChartMode === mode && chartInstance) {
            chartInstance.data.datasets[0].data = chartDataBuffers[mode];
        }
    }

    function scheduleChartUpdate() {
        if (!chartInstance) return;
        if (chartUpdateTimer) clearTimeout(chartUpdateTimer);
        chartUpdateTimer = setTimeout(() => {
            chartInstance.update('none');
            chartUpdateTimer = null;
        }, 200); // debounce 200ms
    }

    function showLogFlood(flag) {
        const badge = document.getElementById('health-badge');
        if (flag) {
            badge.style.display = 'block';
            badge.title = 'Log flood detected: dashboard throttled';
            badge.style.background = '#dc2626';
        } else if (!lastHealth || !badge.classList.contains('health-warn')) {
            badge.style.display = 'none';
            badge.style.background = '';
            badge.title = '';
        }
    }

    function updateUpdatedAt() {
        const el = document.getElementById('status-updated');
        if (!el || !lastStatusTs) return;
        const diff = Math.max(0, Date.now() - lastStatusTs);
        const sec = Math.round(diff / 1000);
        if (sec < 60) el.innerText = `Updated ${sec}s ago`;
        else {
            const m = Math.floor(sec / 60);
            el.innerText = `Updated ${m}m ago`;
        }
    }

    setInterval(updateUpdatedAt, 5000);

    function updateTradingBot(bot) {
        const pill = document.getElementById('bot-pill');
        const text = document.getElementById('bot-status-text');
        if (!pill || !text) return;
        const online = bot && bot.is_running;
        const blocked = bot && bot.life_blocked;
        const equity = bot && bot.equity !== undefined && bot.equity !== null ? ` | Eq $${Number(bot.equity).toFixed(0)}` : '';
        text.innerText = online ? (blocked ? 'HF Bot BLOCKED' : 'HF Bot ONLINE') + equity : 'HF Bot OFFLINE';
        if (online && !blocked) {
            pill.className = 'status-badge online';
        } else if (online && blocked) {
            pill.className = 'status-badge warn';
        } else {
            pill.className = 'status-badge offline';
        }
    }

    async function tradingCommand(cmd) {
        const resEl = document.getElementById('cmd-result');
        resEl.innerText = '...';
        try {
            const res = await fetch('/api/trading/command', {
                method: 'POST',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ cmd })
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                resEl.innerText = `Sent: ${cmd}`;
                setTimeout(() => resEl.innerText = '', 2000);
            } else {
                resEl.innerText = data.message || 'Failed';
            }
        } catch (e) {
            resEl.innerText = 'Network error';
        }
    }

    function renderDevices(devices) {
        const container = document.getElementById('device-container');
        container.innerHTML = "";
        
        let hasDevices = false;
        Object.entries(devices).forEach(([type, rooms]) => {
            Object.entries(rooms).forEach(([room, state]) => {
                hasDevices = true;
                let icon = 'fa-bolt';
                if(type === 'lampu') icon = 'fa-lightbulb';
                if(type === 'ac') icon = 'fa-snowflake';
                
                const div = document.createElement('div');
                div.className = `dev-item ${state.power ? 'active' : ''}`;
                div.onclick = () => controlDevice(type, room, state.power);
                div.innerHTML = `
                    <i class="fa-solid ${icon}"></i>
                    <div class="dev-name">${room}</div>
                `;
                container.appendChild(div);
            });
        });
        
        if (!hasDevices) {
            container.innerHTML = `<div class="dev-item dev-item-empty"><div class="dev-name">NO DEVICES</div></div>`;
        }
    }

    function addLog(msg) {
        const box = document.getElementById('log-box');
        const div = document.createElement('div');
        div.className = 'log-item';
        div.innerText = msg;
        box.prepend(div); 
    }

    function addLogBatch(logs) {
        const box = document.getElementById('log-box');
        box.innerHTML = ""; 
        [...logs].reverse().forEach(log => {
            const div = document.createElement('div');
            div.className = 'log-item';
            div.innerText = log;
            box.appendChild(div);
        });
    }

    let lastHealth = null;
    const HEALTH_THRESHOLDS = { dbRate: 1.0, dbContentionRate: 5.0, loopLag: 200, fwQueue: 10, cpu: 85, mem: 85 };
    const screenerList = document.getElementById('screener-list');
    const screenerStatus = document.getElementById('screener-status');
    const cryptoScreenerList = document.getElementById('crypto-screener-list');
    const cryptoScreenerStatus = document.getElementById('crypto-screener-status');
    const typewriterPhrases = [
        "Control Center - Hedge Fund - Automation",
        "Hedge Fund Ops - Risk Engine Active",
        "Two-Brain System: Logic + LLM",
        "Energy - Security - Insight"
    ];
    let twIndex = 0;
    let twPos = 0;
    let twDeleting = false;

    function tickTypewriter() {
        const el = document.getElementById('tw-text');
        if (!el) return;
        const phrase = typewriterPhrases[twIndex % typewriterPhrases.length];
        if (!twDeleting) {
            twPos++;
            if (twPos >= phrase.length) {
                twDeleting = true;
                setTimeout(tickTypewriter, 900);
                el.textContent = phrase;
                return;
            }
        } else {
            twPos--;
            if (twPos <= 0) {
                twDeleting = false;
                twIndex++;
            }
        }
        el.textContent = phrase.substring(0, twPos);
        const delay = twDeleting ? 40 : 75;
        setTimeout(tickTypewriter, delay);
    }
    function runIntroTyping() {
        setTimeout(tickTypewriter, 400);
    }

    function updateHealth(health) {
        lastHealth = health;
        const dbLocks = health.db_lock_count ?? 0;
        const dbRate = health.db_lock_rate_per_min ?? 0;
        const dbContention = health.db_write_lock_contention_count ?? 0;
        const dbContentionRate = health.db_write_lock_contention_rate_per_min ?? 0;
        const lagMs = health.loop_lag_ms ?? 0;
        const fwDepth = health.firewall_queue_depth;
        const cpu = health.cpu_pct;
        const mem = health.mem_pct;

        const dbLocksEl = document.getElementById('health-db-locks');
        const dbRateEl = document.getElementById('health-db-rate');
        const dbContentionEl = document.getElementById('health-db-contention');
        const dbContentionRateEl = document.getElementById('health-db-contention-rate');
        const lagEl = document.getElementById('health-loop-lag');
        const fwEl = document.getElementById('health-fw-queue');
        const cpuEl = document.getElementById('health-cpu');
        const memEl = document.getElementById('health-mem');

        dbLocksEl.innerText = dbLocks;
        dbRateEl.innerText = `${dbRate.toFixed(2)}/min`;
        dbContentionEl.innerText = dbContention;
        dbContentionRateEl.innerText = `${dbContentionRate.toFixed(2)}/min`;
        lagEl.innerText = `${Math.round(lagMs)}ms`;
        fwEl.innerText = (fwDepth === null || fwDepth === undefined) ? "n/a" : fwDepth;
        cpuEl.innerText = (cpu === null || cpu === undefined) ? "n/a" : `${Math.round(cpu)}%`;
        memEl.innerText = (mem === null || mem === undefined) ? "n/a" : `${Math.round(mem)}%`;

        applyHealthClass(dbRateEl, dbRate > HEALTH_THRESHOLDS.dbRate);
        applyHealthClass(dbContentionRateEl, dbContentionRate > HEALTH_THRESHOLDS.dbContentionRate);
        applyHealthClass(lagEl, lagMs > HEALTH_THRESHOLDS.loopLag);
        applyHealthClass(fwEl, fwDepth !== null && fwDepth !== undefined && fwDepth > HEALTH_THRESHOLDS.fwQueue);
        applyHealthClass(cpuEl, cpu !== null && cpu !== undefined && cpu > HEALTH_THRESHOLDS.cpu);
        applyHealthClass(memEl, mem !== null && mem !== undefined && mem > HEALTH_THRESHOLDS.mem);

        updateHealthDrawer();
        updateHealthBadge(dbRate, dbContentionRate, lagMs, fwDepth, cpu, mem);
    }

    function applyHealthClass(el, isBad) {
        el.classList.remove('health-warn', 'health-bad');
        if (!isBad) return;
        el.classList.add('health-warn');
        if (isBad && (el.id === 'health-loop-lag' || el.id === 'health-db-rate' || el.id === 'health-fw-queue')) {
            el.classList.add('health-bad');
        }
    }

    function toggleHealthDrawer(open) {
        const overlay = document.getElementById('health-drawer-overlay');
        overlay.style.display = open ? 'flex' : 'none';
        if (open) updateHealthDrawer();
    }

    function updateHealthDrawer() {
        if (!lastHealth) return;
        document.getElementById('health-drawer-db-locks').innerText = lastHealth.db_lock_count ?? 0;
        document.getElementById('health-drawer-db-rate').innerText = `${(lastHealth.db_lock_rate_per_min ?? 0).toFixed(2)}/min`;
        document.getElementById('health-drawer-db-contention').innerText = lastHealth.db_write_lock_contention_count ?? 0;
        document.getElementById('health-drawer-db-contention-rate').innerText = `${(lastHealth.db_write_lock_contention_rate_per_min ?? 0).toFixed(2)}/min`;
        document.getElementById('health-drawer-loop-lag').innerText = Math.round(lastHealth.loop_lag_ms ?? 0);
        const fwDepth = lastHealth.firewall_queue_depth;
        document.getElementById('health-drawer-fw-queue').innerText = (fwDepth === null || fwDepth === undefined) ? "n/a" : fwDepth;
        const cpu = lastHealth.cpu_pct;
        const mem = lastHealth.mem_pct;
        document.getElementById('health-drawer-cpu').innerText = (cpu === null || cpu === undefined) ? "n/a" : Math.round(cpu);
        document.getElementById('health-drawer-mem').innerText = (mem === null || mem === undefined) ? "n/a" : Math.round(mem);
        document.getElementById('health-drawer-updated').innerText = new Date().toLocaleTimeString();
    }

    function updateHealthBadge(dbRate, dbContentionRate, lagMs, fwDepth, cpu, mem) {
        const badge = document.getElementById('health-badge');
        const isWarn = (
            dbRate > HEALTH_THRESHOLDS.dbRate ||
            dbContentionRate > HEALTH_THRESHOLDS.dbContentionRate ||
            lagMs > HEALTH_THRESHOLDS.loopLag ||
            (fwDepth !== null && fwDepth !== undefined && fwDepth > HEALTH_THRESHOLDS.fwQueue) ||
            (cpu !== null && cpu !== undefined && cpu > HEALTH_THRESHOLDS.cpu) ||
            (mem !== null && mem !== undefined && mem > HEALTH_THRESHOLDS.mem)
        );
        badge.style.display = isWarn ? 'block' : 'none';
    }

    document.getElementById('health-drawer-overlay').addEventListener('click', (e) => {
        if (e.target.id === 'health-drawer-overlay') {
            toggleHealthDrawer(false);
        }
    });

    // Screener panel controls
    function openScreenerPanel() {
        document.getElementById('screener-panel').classList.add('active');
        fetchScreener(true);
    }
    function closeScreenerPanel() {
        document.getElementById('screener-panel').classList.remove('active');
    }

    function renderScreenerVisual(panelData) {
        const tableBody = document.querySelector('#screener-table tbody');
        if (!tableBody) return;
        const rows = (panelData && panelData.table && panelData.table.rows) || [];
        if (!rows.length) {
            tableBody.innerHTML = '<tr><td colspan="8" class="table-empty">No screener rows.</td></tr>';
        } else {
            const frag = document.createDocumentFragment();
            rows.forEach((row) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${toNum(row.rank, 0)}</td>
                    <td>${row.symbol || '-'}</td>
                    <td>${toNum(row.score, 0).toFixed(1)}</td>
                    <td>${row.setup || '-'}</td>
                    <td>${toNum(row.entry, 0).toFixed(0)}</td>
                    <td>${toNum(row.stop, 0).toFixed(0)}</td>
                    <td>${toNum(row.tp1, 0).toFixed(0)}</td>
                    <td>${toNum(row.rr, 0).toFixed(2)}</td>
                `;
                frag.appendChild(tr);
            });
            tableBody.innerHTML = '';
            tableBody.appendChild(frag);
        }

        const chartCanvas = document.getElementById('screener-score-chart');
        if (!chartCanvas) return;
        const labels = rows.map((row) => row.symbol || '-');
        const scores = rows.map((row) => toNum(row.score, 0));
        if (!screenerScoreChart) {
            screenerScoreChart = new Chart(chartCanvas.getContext('2d'), {
                type: 'bar',
                data: {
                    labels,
                    datasets: [{
                        label: 'Screener Score',
                        data: scores,
                        borderWidth: 1,
                        borderColor: '#D4AF37',
                        backgroundColor: 'rgba(212,175,55,0.25)',
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                    },
                    scales: {
                        x: {
                            ticks: { color: '#9ca3af', maxRotation: 0, autoSkip: true },
                            grid: { color: '#111' },
                        },
                        y: {
                            beginAtZero: true,
                            max: 100,
                            ticks: { color: '#9ca3af' },
                            grid: { color: '#111' },
                        }
                    }
                }
            });
        } else {
            screenerScoreChart.data.labels = labels;
            screenerScoreChart.data.datasets[0].data = scores;
            screenerScoreChart.update('none');
        }
    }

    async function fetchScreener(updatePanel = false) {
        screenerStatus.innerText = 'Loading...';
        screenerList.innerHTML = '';
        const panelList = document.getElementById('screener-list-panel');
        const panelStatus = document.getElementById('screener-status-panel');
        if (panelList) panelList.innerHTML = '';
        if (panelStatus) panelStatus.innerText = 'Loading...';
        try {
            const res = await fetch('/api/screener/visualization?refresh=1', { headers: authHeaders() });
            const data = await res.json();
            const items = data.results || [];
            const isRunning = !!data.running;
            const updatedAt = Number(data.updated_at || 0);
            const renderList = (target) => {
                if (!target) return;
                if (!items.length) {
                    const text = isRunning
                        ? 'Screener sedang memproses di background...'
                        : (data.error || data.text || 'Tidak ada saham yang lolos filter.');
                    target.innerHTML = `<div class="log-item">${text}</div>`;
                    return;
                }
                const frag = document.createDocumentFragment();
                items.forEach((r) => {
                    const close = toNum(r.close, 0);
                    const volr = toNum(r.vol_ratio, 0);
                    const atr = toNum(r.atr_pct, 0);
                    const entry = toNum(r.entry, 0);
                    const div = document.createElement('div');
                    div.className = 'log-item';
                    div.innerHTML = `<strong>${r.symbol}</strong> | Close ${close.toFixed(0)} | Vol ${volr.toFixed(1)}x | ATR ${atr.toFixed(2)}% | Entry ${entry.toFixed(0)}`;
                    frag.appendChild(div);
                });
                target.appendChild(frag);
            };
            renderList(screenerList);
            if (updatePanel && panelList) renderList(panelList);
            renderScreenerVisual(data);
            let ts = `Updated ${new Date().toLocaleTimeString()}`;
            if (updatedAt > 0) {
                ts = `Updated ${new Date(updatedAt * 1000).toLocaleTimeString()}`;
            }
            if (isRunning) {
                ts = `${ts} | Running`;
            }
            screenerStatus.innerText = ts;
            if (panelStatus) panelStatus.innerText = ts;
        } catch (e) {
            screenerStatus.innerText = 'Error fetch screener';
            if (panelStatus) panelStatus.innerText = 'Error fetch screener';
            console.error(e);
        }
    }

    async function fetchCryptoScreener() {
        if (!cryptoScreenerStatus || !cryptoScreenerList) return;
        cryptoScreenerStatus.innerText = 'Loading...';
        cryptoScreenerList.innerHTML = '';
        try {
            const res = await fetch('/api/crypto/visualization?refresh=1', { headers: authHeaders() });
            const data = await res.json();
            const items = data.results || [];
            const isRunning = !!data.running;
            const updatedAt = Number(data.updated_at || 0);

            if (!items.length) {
                const text = isRunning
                    ? 'Crypto screener sedang memproses...'
                    : (data.error || data.text || 'Tidak ada setup crypto yang lolos filter.');
                cryptoScreenerList.innerHTML = `<div class="log-item">${text}</div>`;
            } else {
                const frag = document.createDocumentFragment();
                items.forEach((r) => {
                    const close = toNum(r.close, 0);
                    const atr = toNum(r.atr_pct, 0);
                    const volr = toNum(r.vol_ratio, 0);
                    const div = document.createElement('div');
                    div.className = 'log-item';
                    div.innerHTML = `<strong>${r.symbol}</strong> | Close ${close.toFixed(5)} | Vol ${volr.toFixed(1)}x | ATR ${atr.toFixed(2)}%`;
                    frag.appendChild(div);
                });
                cryptoScreenerList.appendChild(frag);
            }

            let ts = `Updated ${new Date().toLocaleTimeString()}`;
            if (updatedAt > 0) {
                ts = `Updated ${new Date(updatedAt * 1000).toLocaleTimeString()}`;
            }
            if (isRunning) ts = `${ts} | Running`;
            cryptoScreenerStatus.innerText = ts;
        } catch (e) {
            cryptoScreenerStatus.innerText = 'Error fetch crypto screener';
            console.error(e);
        }
    }

    async function sendCommand(text = null) {
        const input = document.getElementById('cmd-input');
        const cmd = String(text || input.value || '').trim();
        if (!cmd) return;

        if(!text) {
             if (!commandHistory.length || commandHistory[commandHistory.length - 1] !== cmd) {
                 commandHistory.push(cmd);
                 if (commandHistory.length > 60) commandHistory.shift();
             }
             commandHistoryCursor = commandHistory.length;
             const history = document.getElementById('ai-response');
             history.innerHTML += `<div class="msg-user">${cmd}</div>`;
             history.scrollTop = history.scrollHeight;
             input.value = "";
        }

        try {
            const res = await fetch('/api/control', {
                method: 'POST',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ command: cmd })
            });
            if (res.status === 401) {
                window.location.href = '/login';
                return;
            }
            if (!res.ok) {
                addLog(`Command failed: ${res.status}`);
                return;
            }
            const data = await res.json().catch(() => ({}));
            if (!socketConnected) {
                const msg = String((data && data.message) || '').trim() || 'Command diterima, menunggu output.';
                const history = document.getElementById('ai-response');
                history.innerHTML += `<div class="msg-ai">${msg}</div>`;
                history.scrollTop = history.scrollHeight;
            }
        } catch (e) { 
            addLog("Error sending command");
            console.error(e); 
        }
    }

    async function controlDevice(type, room, isOn) {
        const action = isOn ? "off" : "on";
        try {
            const res = await fetch('/api/device', {
                method: 'POST',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ type, room, action })
            });
            if (res.status === 401) {
                window.location.href = '/login';
                return;
            }
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                addLog(data.message || `Device command failed (${res.status})`);
            }
        } catch (e) {
            addLog("Error controlling device");
            console.error(e);
        }
    }

    function spotifyCmd(action) {
        if(action === 'toggle') sendCommand('toggle music');
    }

    function setupInputListener() {
        document.getElementById('cmd-input').addEventListener('keypress', (e) => {
            if(e.key === 'Enter') sendCommand();
        });
        document.getElementById('cmd-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendCommand();
                return;
            }
            if (e.key === 'ArrowUp') {
                if (!commandHistory.length) return;
                e.preventDefault();
                commandHistoryCursor = Math.max(0, commandHistoryCursor - 1);
                e.currentTarget.value = commandHistory[commandHistoryCursor] || '';
                return;
            }
            if (e.key === 'ArrowDown') {
                if (!commandHistory.length) return;
                e.preventDefault();
                commandHistoryCursor = Math.min(commandHistory.length, commandHistoryCursor + 1);
                if (commandHistoryCursor === commandHistory.length) {
                    e.currentTarget.value = '';
                } else {
                    e.currentTarget.value = commandHistory[commandHistoryCursor] || '';
                }
            }
        });
    }
    
    window.logout = async function() {
        try {
            await fetch('/api/logout', { method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }) });
        } catch (_) {}
        setToken(null);
        try { localStorage.removeItem('elara_token'); } catch (_) {}
        window.location.href = '/login';
    };
