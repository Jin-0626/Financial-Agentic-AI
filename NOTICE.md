# Reference and source attribution

The portfolio workflow and terminal layout reference FinceptTerminal, inspected at
`sample/FinceptTerminal-main/fincept-qt/src/screens/portfolio` and
`src/services/portfolio/PortfolioService_ImportExport.cpp`.
The React interface and Rust accounting implementation were written for this project;
Qt/C++ source was not copied into them.

Curated provider scripts under `scripts/data_sources/` are copied from FinceptTerminal
and retain their AGPL-3.0-or-later license, source manifest and attribution. Preserve
those files and notices when distributing the scripts.

Upstream: https://github.com/Fincept-Corporation/FinceptTerminal

The default Obsidian color and font tokens follow the sample ThemeManager.cpp.
The desktop crate preserves AGPL-3.0-or-later licensing for the migrated terminal.
The content-based sample reference and source inventory are in docs/migration/reference.json.

The broader provider/support source collection under scripts/data_sources/ is copied from FinceptTerminal with its upstream LICENSE and content hashes. These files are not automatically executable by agents. Preserve attribution and applicable source-distribution obligations.
