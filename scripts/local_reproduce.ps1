Write-Host "=================================================="
Write-Host " GridWise LLM — Automated Local Reproduction"
Write-Host "=================================================="

# 1. Ensure Python 3
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python is required."
    exit 1
}

# 2. Virtual Environment
Write-Host "[1/4] Creating and activating virtual environment..."
python -m venv venv_reproduce
.\venv_reproduce\Scripts\Activate.ps1

# 3. Dependencies
Write-Host "[2/4] Installing dependencies..."
pip install --upgrade pip | Out-Null
pip install -r requirements.txt | Out-Null
pip install httpx | Out-Null

# 4. Start the server in background
Write-Host "[3/4] Starting FastAPI server..."
Start-Process -NoNewWindow -FilePath "uvicorn" -ArgumentList "app.main:app --host 127.0.0.1 --port 8000" -PassThru | Set-Variable -Name ServerProc

# Give it a moment to boot
Start-Sleep -Seconds 3

# 5. Health Check
Write-Host "[4/4] Verifying /health..."
$Health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get -ErrorAction SilentlyContinue
if ($Health.status -ne "ok") {
    Write-Host "❌ Health check failed"
    Stop-Process -Id $ServerProc.Id -Force
    exit 1
}
Write-Host "✅ Health check passed!"

# 6. Execute public sample request
Write-Host "`n=================================================="
Write-Host " Submitting Public Sample Request (Problem Statement)"
Write-Host "=================================================="

$SamplePayload = @"
{
  "scenario_id": "bup-prelim-sample-01",
  "operator_notes": [
    "Solar output will drop to about 20% from 1 PM to 3 PM.",
    "Do not charge the battery between 2 PM and 4 PM.",
    "Maintain a minimum battery reserve of 50 kWh from 6 PM until 9 PM.",
    "The cafeteria menu changes tomorrow."
  ],
  "battery": {
    "capacity_kwh": 100.0,
    "initial_energy_kwh": 50.0,
    "minimum_energy_kwh": 10.0,
    "max_charge_kwh_per_hour": 50.0,
    "max_discharge_kwh_per_hour": 50.0
  },
  "hours": [
    {"hour": 0, "demand_kwh": 20.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
    {"hour": 1, "demand_kwh": 20.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
    {"hour": 2, "demand_kwh": 20.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
    {"hour": 3, "demand_kwh": 20.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
    {"hour": 4, "demand_kwh": 20.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
    {"hour": 5, "demand_kwh": 25.0, "solar_kwh": 5.0, "tariff_bdt_per_kwh": 5.0},
    {"hour": 6, "demand_kwh": 30.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 8.0},
    {"hour": 7, "demand_kwh": 40.0, "solar_kwh": 15.0, "tariff_bdt_per_kwh": 8.0},
    {"hour": 8, "demand_kwh": 50.0, "solar_kwh": 25.0, "tariff_bdt_per_kwh": 10.0},
    {"hour": 9, "demand_kwh": 60.0, "solar_kwh": 40.0, "tariff_bdt_per_kwh": 10.0},
    {"hour": 10, "demand_kwh": 65.0, "solar_kwh": 50.0, "tariff_bdt_per_kwh": 12.0},
    {"hour": 11, "demand_kwh": 70.0, "solar_kwh": 60.0, "tariff_bdt_per_kwh": 12.0},
    {"hour": 12, "demand_kwh": 75.0, "solar_kwh": 65.0, "tariff_bdt_per_kwh": 12.0},
    {"hour": 13, "demand_kwh": 70.0, "solar_kwh": 65.0, "tariff_bdt_per_kwh": 15.0},
    {"hour": 14, "demand_kwh": 65.0, "solar_kwh": 50.0, "tariff_bdt_per_kwh": 15.0},
    {"hour": 15, "demand_kwh": 60.0, "solar_kwh": 40.0, "tariff_bdt_per_kwh": 15.0},
    {"hour": 16, "demand_kwh": 55.0, "solar_kwh": 25.0, "tariff_bdt_per_kwh": 15.0},
    {"hour": 17, "demand_kwh": 50.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 20.0},
    {"hour": 18, "demand_kwh": 60.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 20.0},
    {"hour": 19, "demand_kwh": 70.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 20.0},
    {"hour": 20, "demand_kwh": 80.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 20.0},
    {"hour": 21, "demand_kwh": 60.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 15.0},
    {"hour": 22, "demand_kwh": 40.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 10.0},
    {"hour": 23, "demand_kwh": 25.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 8.0}
  ]
}
"@

$Response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/optimize-energy" -Method Post -Body $SamplePayload -ContentType "application/json"
$Response | ConvertTo-Json -Depth 5

Stop-Process -Id $ServerProc.Id -Force
Remove-Item -Recurse -Force venv_reproduce

Write-Host "=================================================="
Write-Host "✅ Local reproduction completed successfully!"
Write-Host "=================================================="
