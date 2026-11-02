# SBSM project instructions

- Work only in this project folder unless the user explicitly requests otherwise.
- After every meaningful change, run the relevant checks and make a descriptive local Git commit. GitHub authentication/publishing is temporarily deferred at the user's explicit request; do not retry login or push until the user asks to resume. Once resumed, push meaningful commits to the configured remote as originally requested.
- Keep commits focused: distinguish signal-contract work, execution/accounting changes, tests, and operational documentation when useful.
- Do not force-push, rewrite existing history, or overwrite unrelated user changes without explicit authorization.
- Never commit or push .env files, broker credentials, session tokens, databases, logs, caches, or private trading/account data. Keep .env.example free of actual credentials.
- If a push is blocked by authentication, permissions, or a missing remote, keep local commits and report the exact blocker. Do not claim GitHub is updated unless the remote commit is verified.
- This milestone is paper execution only. Preserve the ban on live order placement until the user explicitly requests that implementation.
- Preserve the agreed trading rules: equities, Zerodha market data, approved 20-stock universe, 2H signals, long only, one position per stock, overnight holding until exit, no automatic stop-loss or daily-loss exits.
- Allocate 12% of initial capital plus net realized P&L, excluding unrealized changes. Admit entries FIFO, atomically reserve funds, permanently record skipped entries, and match exits by trade cycle.
- Run the project's virtual-environment pytest suite for financial state-machine changes. Keep simulation assumptions and verification limits documented in SERVER.md.
