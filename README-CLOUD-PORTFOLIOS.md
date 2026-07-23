# Cloud portfolio backend

This branch includes the backend pieces for authenticated cloud portfolios:

- `supabase/schema.sql` creates `portfolios` and `purchases` with per-user Row Level Security.
- `portfolio/cloud_portfolios.py` provides authenticated REST access, session refresh, and CRUD helpers.
- `portfolio/portfolio_chat.py` extracts purchase details from natural-language messages.
- `portfolio/portfolio_metrics.py` calculates current prices and returns while excluding incomplete purchases.

## One-time Supabase setup

1. Open the Supabase SQL Editor.
2. Run the complete contents of `supabase/schema.sql`.
3. Enter the project URL and publishable key in Stock Agent's Account tab.

Never use a service-role key in the desktop application. The schema enables
Row Level Security so authenticated users can access only their own portfolios
and purchases.

The current GUI exposes Supabase authentication in the Account tab. The cloud
portfolio client is kept as a tested backend layer; portfolio UI synchronization
can be added without replacing the existing LSEG research interface.
