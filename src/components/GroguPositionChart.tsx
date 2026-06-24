import React, { useState, useEffect } from 'react';
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';

interface KlineData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface PositionLevels {
  call_entry: number;
  call_sl: number;
  call_tp1: number;
  call_tp2: number;
  put_entry: number;
  put_sl: number;
  put_tp1: number;
  put_tp2: number;
}

interface GroguPosition {
  cycle_id: number;
  symbol: string;
  side: string;
  entry_price: number;
  entry_time: number;
  expiry_time: number;
  klines: KlineData[];
  levels: PositionLevels;
  call_leg_status: string;
  put_leg_status: string;
  current_price: number;
}

const GroguPositionChart: React.FC<{ cycleId?: number }> = ({ cycleId }) => {
  const [position, setPosition] = useState<GroguPosition | null>(null);
  const [chartData, setChartData] = useState<any[]>([]);
  const [timeLeft, setTimeLeft] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch position data
  useEffect(() => {
    const fetchPosition = async () => {
      try {
        const url = cycleId
          ? `http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true&cycle_id=${cycleId}`
          : `http://187.127.114.34:8000/api/v1/grogu/positions?with_levels=true`;

        const response = await fetch(url);
        if (!response.ok) throw new Error('Failed to fetch position');

        const data = await response.json();
        const latestPosition = Array.isArray(data) ? data[0] : data;
        setPosition(latestPosition);

        // Transform klines for chart
        if (latestPosition.klines) {
          const transformed = latestPosition.klines.map((k: KlineData) => ({
            time: new Date(k.time * 1000).toLocaleTimeString(),
            timeMs: k.time,
            price: k.close,
            open: k.open,
            high: k.high,
            low: k.low,
          }));
          setChartData(transformed);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchPosition();
    const interval = setInterval(fetchPosition, 5000); // Poll every 5s
    return () => clearInterval(interval);
  }, [cycleId]);

  // Update expiry timer
  useEffect(() => {
    if (!position) return;

    const updateTimer = () => {
      const now = Math.floor(Date.now() / 1000);
      const secondsLeft = position.expiry_time - now;

      if (secondsLeft <= 0) {
        setTimeLeft('EXPIRED');
        return;
      }

      const hours = Math.floor(secondsLeft / 3600);
      const minutes = Math.floor((secondsLeft % 3600) / 60);
      const seconds = secondsLeft % 60;

      setTimeLeft(`${hours}h ${minutes}m ${seconds}s`);
    };

    updateTimer();
    const interval = setInterval(updateTimer, 1000);
    return () => clearInterval(interval);
  }, [position]);

  if (loading) return <div className="p-4">Loading position data...</div>;
  if (error) return <div className="p-4 text-red-500">Error: {error}</div>;
  if (!position) return <div className="p-4">No position data</div>;

  const callSlHit = position.current_price >= position.levels.call_sl;
  const putSlHit = position.current_price <= position.levels.put_sl;
  const bothTp2 =
    position.current_price >= position.levels.call_tp2 - 2 &&
    position.current_price <= position.levels.put_tp2 + 2;

  return (
    <div className="w-full bg-gradient-to-br from-slate-900 to-slate-800 p-6 rounded-lg border border-slate-700">
      {/* Header */}
      <div className="flex justify-between items-start mb-4">
        <div>
          <h2 className="text-2xl font-bold text-white">
            {position.symbol} {position.side === 'LONG' ? '📈' : '📉'} Cycle #{position.cycle_id}
          </h2>
          <p className="text-slate-400">24h Straddle Strategy</p>
        </div>
        <div className="text-right">
          <div className="text-3xl font-mono text-cyan-400">${position.current_price.toFixed(2)}</div>
          <div className={`text-lg font-bold ${timeLeft === 'EXPIRED' ? 'text-red-500' : 'text-yellow-400'}`}>
            ⏱️ {timeLeft}
          </div>
        </div>
      </div>

      {/* Status Indicators */}
      <div className="grid grid-cols-4 gap-3 mb-4">
        <div className={`p-2 rounded ${callSlHit ? 'bg-red-900/30 border border-red-500' : 'bg-slate-700 border border-slate-600'}`}>
          <div className="text-xs text-slate-400">Call SL</div>
          <div className={`font-mono font-bold ${callSlHit ? 'text-red-400' : 'text-white'}`}>
            ${position.levels.call_sl.toFixed(2)}
            {callSlHit && ' 🔴'}
          </div>
        </div>

        <div className={`p-2 rounded ${putSlHit ? 'bg-red-900/30 border border-red-500' : 'bg-slate-700 border border-slate-600'}`}>
          <div className="text-xs text-slate-400">Put SL</div>
          <div className={`font-mono font-bold ${putSlHit ? 'text-red-400' : 'text-white'}`}>
            ${position.levels.put_sl.toFixed(2)}
            {putSlHit && ' 🔴'}
          </div>
        </div>

        <div className={`p-2 rounded ${bothTp2 ? 'bg-green-900/30 border border-green-500' : 'bg-slate-700 border border-slate-600'}`}>
          <div className="text-xs text-slate-400">Both TP2</div>
          <div className={`font-mono font-bold ${bothTp2 ? 'text-green-400' : 'text-white'}`}>
            ±$2 @ {position.levels.call_tp2.toFixed(2)}
            {bothTp2 && ' 🟢'}
          </div>
        </div>

        <div className="p-2 rounded bg-slate-700 border border-slate-600">
          <div className="text-xs text-slate-400">Entry</div>
          <div className="font-mono font-bold text-blue-400">${position.entry_price.toFixed(2)}</div>
        </div>
      </div>

      {/* Chart */}
      <div className="mb-4 bg-slate-950 rounded-lg p-2 border border-slate-700">
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
            <XAxis
              dataKey="time"
              stroke="#94a3b8"
              tick={{ fontSize: 12 }}
              interval={Math.max(0, Math.floor(chartData.length / 8))}
            />
            <YAxis
              stroke="#94a3b8"
              domain="dataMin - 10 dataMax + 10"
              tick={{ fontSize: 12 }}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#1e293b',
                border: '1px solid #475569',
                borderRadius: '4px',
              }}
              labelStyle={{ color: '#94a3b8' }}
              formatter={(value: number) => `$${value.toFixed(2)}`}
            />

            {/* Price Line */}
            <Line
              type="monotone"
              dataKey="price"
              stroke="#06b6d4"
              dot={false}
              strokeWidth={2}
              name="ETH Price"
              isAnimationActive={false}
            />

            {/* Call SL */}
            <ReferenceLine
              y={position.levels.call_sl}
              stroke={callSlHit ? '#ef4444' : '#f97316'}
              strokeDasharray="5 5"
              strokeWidth={2}
              label={{
                value: `Call SL: $${position.levels.call_sl.toFixed(0)}`,
                position: 'right',
                fill: callSlHit ? '#ef4444' : '#f97316',
                fontSize: 12,
              }}
            />

            {/* Put SL */}
            <ReferenceLine
              y={position.levels.put_sl}
              stroke={putSlHit ? '#ef4444' : '#f97316'}
              strokeDasharray="5 5"
              strokeWidth={2}
              label={{
                value: `Put SL: $${position.levels.put_sl.toFixed(0)}`,
                position: 'right',
                fill: putSlHit ? '#ef4444' : '#f97316',
                fontSize: 12,
              }}
            />

            {/* Call TP2 */}
            <ReferenceLine
              y={position.levels.call_tp2}
              stroke="#10b981"
              strokeDasharray="3 3"
              strokeWidth={2}
              label={{
                value: `Call TP2: $${position.levels.call_tp2.toFixed(0)}`,
                position: 'right',
                fill: '#10b981',
                fontSize: 12,
              }}
            />

            {/* Put TP2 */}
            <ReferenceLine
              y={position.levels.put_tp2}
              stroke="#10b981"
              strokeDasharray="3 3"
              strokeWidth={2}
              label={{
                value: `Put TP2: $${position.levels.put_tp2.toFixed(0)}`,
                position: 'right',
                fill: '#10b981',
                fontSize: 12,
              }}
            />

            {/* Entry */}
            <ReferenceLine
              y={position.entry_price}
              stroke="#3b82f6"
              strokeWidth={2}
              label={{
                value: `Entry: $${position.entry_price.toFixed(0)}`,
                position: 'right',
                fill: '#3b82f6',
                fontSize: 12,
              }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Scenario Analysis */}
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-red-900/20 border border-red-600 rounded p-3">
          <div className="text-sm font-bold text-red-400 mb-2">🔴 Call SL Scenario</div>
          <p className="text-xs text-slate-300">
            If ETH spikes to <span className="font-mono font-bold">${position.levels.call_sl.toFixed(2)}</span>
            <br />→ Call leg stopped, Put continues
          </p>
        </div>

        <div className="bg-red-900/20 border border-red-600 rounded p-3">
          <div className="text-sm font-bold text-red-400 mb-2">🔴 Put SL Scenario</div>
          <p className="text-xs text-slate-300">
            If ETH crashes to <span className="font-mono font-bold">${position.levels.put_sl.toFixed(2)}</span>
            <br />→ Put leg stopped, Call continues
          </p>
        </div>

        <div className="bg-green-900/20 border border-green-600 rounded p-3">
          <div className="text-sm font-bold text-green-400 mb-2">🟢 Both TP2</div>
          <p className="text-xs text-slate-300">
            If ETH stays near <span className="font-mono font-bold">${position.entry_price.toFixed(2)}</span>
            <br />→ Both legs hit TP2 = max profit
          </p>
        </div>
      </div>

      {/* Leg Status */}
      <div className="mt-4 grid grid-cols-2 gap-3">
        <div className={`p-3 rounded border ${position.call_leg_status === 'OPEN' ? 'border-blue-500 bg-blue-900/20' : 'border-slate-600 bg-slate-700'}`}>
          <div className="text-xs text-slate-400">Call Leg</div>
          <div className={`font-bold ${position.call_leg_status === 'OPEN' ? 'text-blue-400' : 'text-slate-400'}`}>
            {position.call_leg_status}
          </div>
        </div>
        <div className={`p-3 rounded border ${position.put_leg_status === 'OPEN' ? 'border-blue-500 bg-blue-900/20' : 'border-slate-600 bg-slate-700'}`}>
          <div className="text-xs text-slate-400">Put Leg</div>
          <div className={`font-bold ${position.put_leg_status === 'OPEN' ? 'text-blue-400' : 'text-slate-400'}`}>
            {position.put_leg_status}
          </div>
        </div>
      </div>
    </div>
  );
};

export default GroguPositionChart;
