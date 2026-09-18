#!/usr/bin/env bash
set -e

echo "=================================================="
echo " GridWise LLM — Automated Local Reproduction"
echo "=================================================="

# 1. Ensure Python 3
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is required."
    exit 1
fi

# 2. Virtual Environment
echo "[1/4] Creating and activating virtual environment..."
python3 -m venv venv_reproduce
source venv_reproduce/bin/activate

# 3. Dependencies
echo "[2/4] Installing dependencies..."
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt > /dev/null 2>&1
pip install httpx > /dev/null 2>&1

# 4. Start the server in background
echo "[3/4] Starting FastAPI server..."
uvicorn app.main:app --host 127.0.0.1 --port 8000 &
SERVER_PID=$!

# Give it a moment to boot
sleep 3

# 5. Health Check
echo "[4/4] Verifying /health..."
HEALTH=$(curl -s http://127.0.0.1:8000/health)
if [[ "$HEALTH" != *"ok"* ]]; then
    echo "❌ Health check failed: $HEALTH"
    kill $SERVER_PID
    exit 1
fi
echo "✅ Health check passed!"

# 6. Execute public sample request
echo ""
echo "=================================================="
echo " Submitting Public Sample Request (Problem Statement)"
echo "=================================================="

cat <<EOF > sample_payload.json
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
EOF

RESPONSE=$(curl -s -X POST http://127.0.0.1:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @sample_payload.json)

echo "$RESPONSE" | python3 -m json.tool

kill $SERVER_PID
rm sample_payload.json
rm -rf venv_reproduce

echo "=================================================="
echo "✅ Local reproduction completed successfully!"
echo "=================================================="
