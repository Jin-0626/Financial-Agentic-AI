import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
export function PerformanceChart({
  chart,
}: {
  chart: { date: string; Portfolio: number; Benchmark: number }[];
}) {
  return (
    <div className="chart">
      <ResponsiveContainer>
        <LineChart data={chart}>
          <CartesianGrid stroke="#252b35" strokeDasharray="3 3" />
          <XAxis dataKey="date" minTickGap={55} />
          <YAxis domain={["auto", "auto"]} />
          <Tooltip />
          <Line
            type="monotone"
            dataKey="Portfolio"
            stroke="#ff9f43"
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="Benchmark"
            stroke="#6aa5ff"
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
