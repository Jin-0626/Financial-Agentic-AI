import { invoke, isTauri } from "@tauri-apps/api/core";
export const desktop = isTauri();
export const openWorkspace = (module: string) =>
  invoke<void>("open_workspace", { module });
export const openPortfolioFile = () =>
  invoke<string | null>("open_portfolio_file");
