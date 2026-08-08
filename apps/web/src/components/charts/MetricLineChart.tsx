"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function MetricLineChart({
  data,
  color = "#0f9f8a",
}: {
  data: Array<{ date: string; value: number }>;
  color?: string;
}) {
  if (!data.length) {
    return <p className="py-10 text-center text-sm text-[var(--muted)]">Insufficient data.</p>;
  }
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={24} />
          <YAxis tick={{ fontSize: 11 }} width={40} />
          <Tooltip />
          <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
