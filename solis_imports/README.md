Put the latest SolisCloud export file in this folder.

Supported file types:
- `.xlsx`
- `.csv`

The importer looks for columns similar to:
- Plant Name / Station Name / System Name
- Status
- Capacity / Capacity (kW)
- Today Generation / Daily Generation / Today Yield
- Weekly Generation / Week Generation
- Total Generation / Total Yield
- Station ID / Plant ID / System ID

Then run:

```bash
.venv/bin/python solis_import_export.py
```

This creates `solis_generation.json`, which is automatically included in the
combined Excel and PDF report.
