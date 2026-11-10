import type { ApexKpiSummary, ElaraDecisionRow } from "./types";

export class ElaraApexDashboardController {
  private activeTimeframe: string = "12m";
  private activeBarWindow: string = "5D";
  private activeNavSection: string = "dashboard";

  public init(): void {
    this.renderReadinessSegments(24, 28);
    this.renderImpressionsBars(this.activeBarWindow);
    this.bindSidebarNavigation();
    this.bindTimeframePills();
    this.bindBarWindowTabs();
    this.bindThemeSwitch();
    this.bindKeyboardShortcuts();
    this.bindTableSearch();
    this.observePortalMetrics();
  }

  private renderReadinessSegments(filledCount: number, totalCount: number = 28): void {
    const container = document.getElementById("apex-credit-bars");
    if (!container) return;
    container.innerHTML = "";
    for (let i = 0; i < totalCount; i++) {
      const bar = document.createElement("span");
      bar.className = i < filledCount ? "apex-seg-bar is-active" : "apex-seg-bar";
      container.appendChild(bar);
    }
  }

  private renderImpressionsBars(windowKey: string): void {
    const container = document.getElementById("apex-impressions-bars");
    if (!container) return;
    constpresets: Record<string, Array<{ total: number; highlight: number; label: string }>> = {
      "5D": [
        { total: 56, highlight: 42, label: "Mon" },
        { total: 74, highlight: 48, label: "Tue" },
        { total: 82, highlight: 52, label: "Wed" },
        { total: 91, highlight: 58, label: "Thu" },
        { total: 79, highlight: 46, label: "Fri" },
        { total: 86, highlight: 54, label: "Sat" },
      ],
      "2W": [
        { total: 62, highlight: 40, label: "D1" },
        { total: 68, highlight: 45, label: "D3" },
        { total: 88, highlight: 60, label: "D6" },
        { total: 76, highlight: 49, label: "D9" },
        { total: 94, highlight: 66, label: "D12" },
        { total: 84, highlight: 55, label: "D14" },
      ],
      "1M": [
        { total: 50, highlight: 34, label: "W1" },
        { total: 72, highlight: 50, label: "W2" },
        { total: 85, highlight: 58, label: "W3" },
        { total: 95, highlight: 68, label: "W4" },
        { total: 81, highlight: 52, label: "W5" },
        { total: 90, highlight: 62, label: "Now" },
      ],
      "6M": [
        { total: 48, highlight: 32, label: "M1" },
        { total: 64, highlight: 44, label: "M2" },
        { total: 79, highlight: 53, label: "M3" },
        { total: 87, highlight: 61, label: "M4" },
        { total: 92, highlight: 65, label: "M5" },
        { total: 96, highlight: 70, label: "M6" },
      ],
      "1Y": [
        { total: 45, highlight: 30, label: "Q1" },
        { total: 66, highlight: 46, label: "Q2" },
        { total: 78, highlight: 54, label: "Q3" },
        { total: 89, highlight: 63, label: "Q4" },
        { total: 93, highlight: 67, label: "YTD" },
        { total: 98, highlight: 72, label: "Now" },
      ],
    };
    const series = presets[windowKey] || presets["5D"];
    container.innerHTML = series
      .map(
        (item) => `
        <div class="apex-candle-col" title="${item.label}: ${item.total}%">
          <div class="apex-candle-pillar" style="height: ${item.total}%">
            <span class="apex-candle-cap"></span>
            <div class="apex-candle-core" style="height: ${item.highlight}%"></div>
          </div>
        </div>`
      )
      .join("");
  }

  private bindSidebarNavigation(): void {
    const navItems = document.querySelectorAll<HTMLElement>("[data-apex-nav]");
    navItems.forEach((btn) => {
      btn.addEventListener("click", () => {
        navItems.forEach((el) => el.classList.remove("is-active"));
        btn.classList.add("is-active");
        const target = btn.getAttribute("data-apex-nav") || "dashboard";
        this.activeNavSection = target;
        this.scrollToSection(target);
      });
    });
  }

  private scrollToSection(section: string): void {
    const map: Record<string, string> = {
      dashboard: "apex-hero-top",
      projects: "apex-market-section",
      calendar: "apex-decisions-section",
      reporting: "apex-legacy-workspace",
      employees: "apex-ledger-card",
      support: "apex-health-section",
      settings: "open-account",
    };
    if (section === "settings") {
      const accountBtn = document.getElementById("open-account");
      if (accountBtn) accountBtn.click();
      return;
    }
    const targetId = map[section];
    const targetEl = targetId ? document.getElementById(targetId) : null;
    if (targetEl) {
      targetEl.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  private bindTimeframePills(): void {
    const pills = document.querySelectorAll<HTMLButtonElement>("[data-apex-range]");
    pills.forEach((pill) => {
      pill.addEventListener("click", () => {
        pills.forEach((p) => p.classList.remove("is-active"));
        pill.classList.add("is-active");
        this.activeTimeframe = pill.getAttribute("data-apex-range") || "12m";
        this.applyRangeMultiplier(this.activeTimeframe);
      });
    });
  }

  private applyRangeMultiplier(range: string): void {
    const revEl = document.getElementById("apex-kpi-pnl");
    const ordEl = document.getElementById("apex-kpi-orders");
    const avgEl = document.getElementById("apex-kpi-winrate");
    const presets: Record<string, { pnl: string; orders: string; edge: string }> = {
      "12m": { pnl: "$14,820", orders: "1,420", edge: "$104.36" },
      "30d": { pnl: "$4,290", orders: "384", edge: "$96.18" },
      "7d": { pnl: "$1,940", orders: "182", edge: "$93.50" },
      "24h": { pnl: "$1,280", orders: "140", edge: "$91.42" },
    };
    const chosen = presets[range] || presets["24h"];
    if (revEl && !revEl.dataset.liveBound) revEl.textContent = chosen.pnl;
    if (ordEl && !ordEl.dataset.liveBound) ordEl.textContent = chosen.orders;
    if (avgEl && !avgEl.dataset.liveBound) avgEl.textContent = chosen.edge;
  }

  private bindBarWindowTabs(): void {
    const tabs = document.querySelectorAll<HTMLButtonElement>("[data-apex-window]");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        tabs.forEach((t) => t.classList.remove("is-active"));
        tab.classList.add("is-active");
        const key = tab.getAttribute("data-apex-window") || "5D";
        this.activeBarWindow = key;
        this.renderImpressionsBars(key);
      });
    });
  }

  private bindThemeSwitch(): void {
    const darkBtn = document.getElementById("apex-theme-dark");
    const lightBtn = document.getElementById("apex-theme-light");
    const legacyToggle = document.getElementById("theme-toggle-btn");

    if (darkBtn && lightBtn) {
      darkBtn.addEventListener("click", () => {
        darkBtn.classList.add("is-active");
        lightBtn.classList.remove("is-active");
        document.body.setAttribute("data-apex-mode", "dark");
        if (document.body.getAttribute("data-theme") !== "tokyo-night" && legacyToggle) {
          legacyToggle.click();
        }
      });
      lightBtn.addEventListener("click", () => {
        lightBtn.classList.add("is-active");
        darkBtn.classList.remove("is-active");
        document.body.setAttribute("data-apex-mode", "light");
        if (document.body.getAttribute("data-theme") === "tokyo-night" && legacyToggle) {
          legacyToggle.click();
        }
      });
    }
  }

  private bindKeyboardShortcuts(): void {
    window.addEventListener("keydown", (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        const searchInput = document.getElementById("quick-command") as HTMLInputElement | null;
        if (searchInput) {
          searchInput.focus();
          searchInput.select();
        }
      }
    });
  }

  private bindTableSearch(): void {
    const searchInput = document.getElementById("apex-table-search") as HTMLInputElement | null;
    if (!searchInput) return;
    searchInput.addEventListener("input", () => {
      const query = searchInput.value.trim().toLowerCase();
      const rows = document.querySelectorAll<HTMLTableRowElement>("#apex-decision-tbody tr");
      rows.forEach((row) => {
        const text = (row.textContent || "").toLowerCase();
        row.style.display = !query || text.includes(query) ? "" : "none";
      });
    });
  }

  private observePortalMetrics(): void {
    const syncFromDom = (): void => {
      const equityRaw = document.getElementById("stat-equity")?.textContent?.trim() || "";
      const winrateRaw = document.getElementById("stat-winrate")?.textContent?.trim() || "";
      const tradesRaw = document.getElementById("stat-trades")?.textContent?.trim() || "";
      const whoamiRaw = document.getElementById("whoami")?.textContent?.trim() || "";
      const cryptoPrice = document.getElementById("user-crypto-price")?.textContent?.trim() || "";
      const cryptoSymbol = document.getElementById("user-crypto-symbol")?.textContent?.trim() || "";

      if (equityRaw && equityRaw !== "-") {
        const totalEquityEl = document.getElementById("apex-total-equity");
        if (totalEquityEl) totalEquityEl.textContent = equityRaw.startsWith("$") ? equityRaw : `$ ${equityRaw}`;
      }
      if (tradesRaw && tradesRaw !== "-") {
        const ordEl = document.getElementById("apex-kpi-orders");
        if (ordEl) {
          ordEl.textContent = tradesRaw;
          ordEl.dataset.liveBound = "1";
        }
      }
      if (winrateRaw && winrateRaw !== "-") {
        const avgEl = document.getElementById("apex-kpi-winrate");
        if (avgEl) {
          avgEl.textContent = winrateRaw;
          avgEl.dataset.liveBound = "1";
        }
      }
      if (whoamiRaw && !whoamiRaw.includes("Memuat")) {
        const profileNameEl = document.getElementById("apex-user-name");
        if (profileNameEl) {
          const cleanName = whoamiRaw.split("(")[0].replace(/User:|Logged in as/i, "").trim();
          if (cleanName) profileNameEl.textContent = cleanName;
        }
      }
      if (cryptoPrice && cryptoPrice !== "-") {
        const impPriceEl = document.getElementById("apex-market-headline-price");
        const impTitleEl = document.getElementById("apex-market-headline-symbol");
        if (impPriceEl) impPriceEl.textContent = cryptoPrice.startsWith("$") ? cryptoPrice : `$${cryptoPrice}`;
        if (impTitleEl && cryptoSymbol && cryptoSymbol !== "-") {
          impTitleEl.textContent = `${cryptoSymbol} • REALTIME PULSE`;
        }
      }
      this.syncDecisionRows();
    };

    window.setInterval(syncFromDom, 2500);
    window.setTimeout(syncFromDom, 800);
  }

  private syncDecisionRows(): void {
    const sourceTbody = document.querySelector("#decision-table tbody");
    const targetTbody = document.getElementById("apex-decision-tbody");
    if (!sourceTbody || !targetTbody) return;

    const sourceRows = Array.from(sourceTbody.querySelectorAll("tr"));
    if (sourceRows.length === 0) return;

    const parsed: ElaraDecisionRow[] = sourceRows.slice(0, 8).map((tr) => {
      const cells = tr.querySelectorAll("td");
      return {
        timestamp: cells[0]?.textContent?.trim() || "Just now",
        symbol: cells[1]?.textContent?.trim() || "BTC/USDT",
        direction: cells[2]?.textContent?.trim() || "LONG",
        status: cells[3]?.textContent?.trim() || "Approved",
        reason: cells[4]?.textContent?.trim() || "reasoned_decision_approved",
      };
    });

    if (parsed.length === 1 && parsed[0].symbol === "") return;

    targetTbody.innerHTML = parsed
      .map((item, idx) => {
        const isApproved = /approved|open|active|buy|long|passed/i.test(item.status);
        const badgeClass = isApproved ? "apex-status-paid" : "apex-status-pending";
        const badgeLabel = isApproved ? "Approved" : item.status || "Hold";
        const handle = `@${item.symbol.toLowerCase().replace(/[^a-z0-9]/g, "")}`;
        const initials = item.symbol.slice(0, 2).toUpperCase() || "EL";
        return `
          <tr>
            <td class="apex-td-check"><input type="checkbox" aria-label="Select row ${idx + 1}"></td>
            <td>
              <div class="apex-user-cell">
                <span class="apex-avatar apex-avatar-${(idx % 3) + 1}">${initials}</span>
                <div>
                  <div class="apex-user-title">${item.symbol} (${item.direction})</div>
                  <div class="apex-user-sub">${handle}</div>
                </div>
              </div>
            </td>
            <td class="apex-td-muted">${item.reason}</td>
            <td class="apex-td-muted">${item.timestamp}</td>
            <td><span class="apex-status-pill ${badgeClass}">• ${badgeLabel}</span></td>
            <td class="apex-td-amount">100.0%</td>
            <td class="apex-td-actions">
              <button type="button" class="apex-icon-btn" title="Inspect trace">✎</button>
            </td>
          </tr>`;
      })
      .join("");
  }
}
