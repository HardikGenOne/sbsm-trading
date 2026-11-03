# SBSM project instructions

- Work only in this project folder unless the user explicitly requests otherwise.
- After every meaningful change, run the relevant checks and make a descriptive local Git commit. The user resumed GitHub login and publishing on 4 November 2026. Push meaningful commits to the configured private repository once authentication succeeds; report any authentication blocker accurately.
- Keep commits focused: distinguish signal-contract work, execution/accounting changes, tests, and operational documentation when useful.
- Do not force-push, rewrite existing history, or overwrite unrelated user changes without explicit authorization.
- Never commit or push .env files, broker credentials, session tokens, databases, logs, caches, or private trading/account data. Keep .env.example free of actual credentials.
- If a push is blocked by authentication, permissions, or a missing remote, keep local commits and report the exact blocker. Do not claim GitHub is updated unless the remote commit is verified.
- This milestone is paper execution only. Preserve the ban on live order placement until the user explicitly requests that implementation.
- Cloud hosting and outbound Telegram trade/system notifications are now authorized. Broker connection remains deferred; GitHub publishing has resumed. Hosting account, target, budget, and Telegram recipient must be resolved before provisioning paid resources or sending messages; enter secrets locally rather than in chat.
- Hosting budget is free only. The user explicitly paused Render connection/deployment until they resume (GitHub source publishing has separately resumed); do not provision paid resources or attempt account connections. Preserve the ledger: free Render has no persistent disk and is not a supported target for the current SQLite stack.
- Keep notification delivery separate from financial processing. Enqueue in the same transaction as the underlying event; network delivery must never hold the account lock. No Telegram trading commands are authorized.
- Preserve the agreed trading rules: equities, Zerodha market data, approved 20-stock universe, 2H signals, long only, one position per stock, overnight holding until exit, no automatic stop-loss or daily-loss exits.
- Allocate 12% of initial capital plus net realized P&L, excluding unrealized changes. Admit entries FIFO, atomically reserve funds, permanently record skipped entries, and match exits by trade cycle.
- Run the project's virtual-environment pytest suite for financial state-machine changes. Keep simulation assumptions and verification limits documented in SERVER.md.
