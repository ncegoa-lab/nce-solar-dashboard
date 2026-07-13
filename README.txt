NCE Solar Dashboard update - auto refresh and selected-plant graphs

Upload/replace these files in the GitHub repository root:

1. solar_live_app.py
2. solar_performance_report_app.py

What changed:
- Live dashboard data refreshes in the background every 1 minute.
- Refresh now button, loading indicator, Last updated time, and refresh warning message added.
- Browser tab inactivity pauses automatic refresh; refresh resumes when the tab is opened again.
- Existing selected plants are preserved during refresh.
- The main Today's Generation graph is replaced by Today's Per-kW Generation for all visible plants.
- Selected Plant Details section follows the plant name clicked in the table.
- Right pane now has two clicked-plant graphs:
  - Today's Generation - Selected Plant as an area graph.
  - Monthly Generation - Selected Plant as daily bars.
- The main Monthly Generation graph remains cumulative for all visible plants.
- Main graph cards are compact with thin bars and internal horizontal scrolling.
- Main graph row uses a 70/30 desktop width split: Today's Per-kW Generation 70%, Monthly Generation 30%.
- Today's Per-kW Generation plant labels have a dedicated visible label area.
- CSV/PDF exports contain graph table data only.
- On app open, previous-day online plant data is detected immediately and a refresh starts automatically.
- Last updated now shows the backend refresh completion time when available.
- Top graph cards use more internal plotting area with reduced padding and optimized label spacing.
- Right-pane selected plant today's generation graph starts from first generating hour and uses a smooth area curve.
- PDF report styling improved: dark-blue table/header backgrounds use white text.
- PDF report chart page now has three clear trend graphs:
  - Today's generation since start, compact area graph.
  - Monthly generation, day-wise.
  - Yearly generation, month-wise.
  - Per-kW generation, year daily compact area graph.
- Month and year selectors added for monthly graph.
- Mobile/iPhone layout is more compact with shorter charts and two-column summary cards.
- Current power is passed through backend data where available.

Render deployment:
1. Replace the two files above in GitHub.
2. Commit changes.
3. Let Render auto-deploy, or press Manual Deploy > Deploy latest commit.
4. Open the app and confirm Build: 2026-07-13-user-change-password-v54.

No new Render environment variables are required.
No database migration is required.
Automatic refresh interval: 60 seconds.
