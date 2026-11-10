export interface ElaraUserProfile {
  email?: string;
  username?: string;
  name?: string;
  role?: string;
}

export interface ElaraDecisionRow {
  ts?: string;
  timestamp?: string;
  symbol: string;
  direction: string;
  status: string;
  reason?: string;
  confidence?: number;
  strategy_id?: string;
  size_pct?: number;
}

export interface ElaraMarketQuote {
  symbol: string;
  price: number;
  change_pct: number;
  updated_at?: string;
  series?: number[];
}

export interface ApexKpiSummary {
  todayPnlFormatted: string;
  todayPnlDeltaPct: string;
  todayOrdersCount: number;
  todayOrdersDeltaPct: string;
  avgEdgeFormatted: string;
  avgEdgeDeltaPct: string;
  totalEquityFormatted: string;
  confidenceScore: number;
  readinessScore: number;
  marketRegimeLabel: string;
}
