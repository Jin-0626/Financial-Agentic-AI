import { useRef, useState } from "react";
import { Document, Holding, Transaction, Preview } from "./types";
export function usePortfolioEditor(
  doc: Document | null,
  setError: (value: string) => void,
) {
  const [dialog, setDialog] = useState("");
  const [editingTrade, setEditingTrade] = useState<Transaction | null>(null);
  const [name, setName] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [benchmark, setBenchmark] = useState("SPY");
  const [query, setQuery] = useState("");
  const [symbols, setSymbols] = useState<
    { symbol: string; name: string; exchange: string }[]
  >([]);
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState("BUY");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState("");
  const [importData, setImportData] = useState("");
  const [mode, setMode] = useState("New");
  const [preview, setPreview] = useState<Preview | null>(null);
  const tradeId = useRef(crypto.randomUUID());
  function open(value: string) {
    tradeId.current = crypto.randomUUID();
    setError("");
    setDialog(value);
    setEditingTrade(null);
    setPreview(null);
    setSymbol("");
    setQuery("");
    setSymbols([]);
    setQuantity("");
    setPrice("");
    setName(doc?.portfolio.name || "");
    setCurrency(doc?.portfolio.currency || "USD");
    setBenchmark(doc?.portfolio.benchmark || "SPY");
  }
  function selectCorrection(trade: Transaction) {
    setEditingTrade(trade);
    setSymbol(trade.symbol);
    setSide(trade.side);
    setQuantity(trade.quantity);
    setPrice(trade.price);
    setDate(trade.date);
    setNotes(trade.notes || "");
  }
  function editHolding(holding: Holding) {
    const trades =
      doc?.transactions.filter((t) => t.symbol === holding.symbol) || [];
    if (!trades.length) return;
    open("Edit holding");
    selectCorrection(trades.find((t) => t.side === "BUY") || trades[0]);
  }
  return {
    dialog,
    setDialog,
    editingTrade,
    name,
    setName,
    currency,
    setCurrency,
    benchmark,
    setBenchmark,
    query,
    setQuery,
    symbols,
    setSymbols,
    symbol,
    setSymbol,
    side,
    setSide,
    quantity,
    setQuantity,
    price,
    setPrice,
    date,
    setDate,
    notes,
    setNotes,
    importData,
    setImportData,
    mode,
    setMode,
    preview,
    setPreview,
    tradeId,
    open,
    selectCorrection,
    editHolding,
  };
}
