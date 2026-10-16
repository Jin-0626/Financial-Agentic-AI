import { Row } from "./types";
export function parseCSV(text: string): Row[] {
  if (new TextEncoder().encode(text).length > 1048576)
    throw new Error("CSV must be smaller than 1 MiB.");
  const lines = text
    .trim()
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/);
  const cells = (line: string) =>
    line.split(",").map((value) => value.trim().replace(/^"(.*)"$/, "$1"));
  const header = cells(lines.shift() || "").map((value) => value.toLowerCase());
  const dateColumn = header.indexOf("date"),
    closeColumn = header.indexOf("close");
  if (
    dateColumn < 0 ||
    closeColumn < 0 ||
    new Set(header).size !== header.length
  )
    throw new Error(
      "CSV needs date and close columns, with dates in YYYY-MM-DD format.",
    );
  if (lines.length < 3 || lines.length > 10000)
    throw new Error("Provide 3 to 10,000 price observations.");
  return lines.map((line, index) => {
    const parts = cells(line),
      date = parts[dateColumn],
      rawClose = parts[closeColumn];
    if (parts.length !== header.length)
      throw new Error(
        `CSV line ${index + 2} has a different number of columns than its header.`,
      );
    const timestamp = new Date(`${date}T00:00:00Z`);
    const close = Number(rawClose);
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(date || "") ||
      !Number.isFinite(timestamp.getTime()) ||
      timestamp.toISOString().slice(0, 10) !== date ||
      !rawClose ||
      !/^[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(rawClose) ||
      !Number.isFinite(close) ||
      close <= 0
    )
      throw new Error(
        `Invalid date or positive closing price on CSV line ${index + 2}.`,
      );
    if (index && date <= cells(lines[index - 1])[dateColumn])
      throw new Error(
        "Dates must be unique and increasing; no rows are sorted or filled automatically.",
      );
    return { date, close };
  });
}

export function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
