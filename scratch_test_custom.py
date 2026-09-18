import asyncio
import json
import os
from pprint import pprint
from dotenv import load_dotenv

load_dotenv(override=True)

from app.llm.interpreter import interpret_operator_notes
from app.schemas.request import BatteryConfig, HourEntry

custom_cases = {
  "cases": [
    {
      "id": "SAMPLE-09",
      "label": "End-of-Day Boundary & Colloquial Fractions",
      "input": {
        "scenario_id": "SAMPLE-09",
        "operator_notes": [
          "Keep a quarter of the battery's maximum capacity on hand from 10 PM until midnight in case of late-night grid instability.",
          "The cafeteria will serve a special dinner tonight."
        ],
        "battery": {
          "capacity_kwh": 240,
          "initial_energy_kwh": 120,
          "minimum_energy_kwh": 40,
          "max_charge_kwh_per_hour": 50,
          "max_discharge_kwh_per_hour": 50
        }
      },
      "expected_output": {
        "directive_interpretation": [
          {
            "note_index": 0,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {
              "hours": [22, 23],
              "minimum_energy_kwh": 60
            }
          },
          {
            "note_index": 1,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None
          }
        ]
      }
    },
    {
      "id": "SAMPLE-10",
      "label": "Total Solar Blackout & Future-Date Distractor",
      "input": {
        "scenario_id": "SAMPLE-10",
        "operator_notes": [
          "A severe dust storm is rolling in; expect exactly zero usable solar power between 1 PM and 4 PM.",
          "Limit grid import to 100 kWh from 9 AM to 11 AM tomorrow morning for planned feeder maintenance."
        ],
        "battery": {
          "capacity_kwh": 200,
          "initial_energy_kwh": 100,
          "minimum_energy_kwh": 30,
          "max_charge_kwh_per_hour": 55,
          "max_discharge_kwh_per_hour": 55
        }
      },
      "expected_output": {
        "directive_interpretation": [
          {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {
              "hours": [13, 14, 15],
              "factor": 0.0
            }
          },
          {
            "note_index": 1,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None
          }
        ]
      }
    },
    {
      "id": "SAMPLE-11",
      "label": "Overlapping Constraints & Implicit Zero",
      "input": {
        "scenario_id": "SAMPLE-11",
        "operator_notes": [
          "The campus must pull absolutely no power from the grid from 6 PM until 8 PM due to a utility test.",
          "We are doing relay maintenance from 7 PM to 9 PM, so do not discharge the battery."
        ],
        "battery": {
          "capacity_kwh": 250,
          "initial_energy_kwh": 125,
          "minimum_energy_kwh": 50,
          "max_charge_kwh_per_hour": 60,
          "max_discharge_kwh_per_hour": 60
        }
      },
      "expected_output": {
        "directive_interpretation": [
          {
            "note_index": 0,
            "applies": True,
            "directive_type": "max_grid_window",
            "structured_adjustment": {
              "hours": [18, 19],
              "max_grid_kwh": 0
            }
          },
          {
            "note_index": 1,
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {
              "hours": [19, 20]
            }
          }
        ]
      }
    }
  ]
}

async def run_tests():
    # Dummy hours just to satisfy the LLM context if it cares
    dummy_hours = [
        HourEntry(hour=h, demand_kwh=100, solar_kwh=50, tariff_bdt_per_kwh=10)
        for h in range(24)
    ]
    
    for case in custom_cases["cases"]:
        print(f"\n--- Testing {case['id']}: {case['label']} ---")
        battery = BatteryConfig.model_validate(case["input"]["battery"])
        notes = case["input"]["operator_notes"]
        
        result = await interpret_operator_notes(notes, dummy_hours, battery)
        
        passed = True
        for expected, actual in zip(case["expected_output"]["directive_interpretation"], result):
            # Check directive_type
            if expected["directive_type"] != actual["directive_type"]:
                print(f"X Note {expected['note_index']} Type mismatch: Expected {expected['directive_type']}, Got {actual['directive_type']}")
                passed = False
            
            # Check structured adjustment
            if expected["structured_adjustment"] != actual.get("structured_adjustment"):
                print(f"X Note {expected['note_index']} Adjustment mismatch: Expected {expected['structured_adjustment']}, Got {actual.get('structured_adjustment')}")
                passed = False
                
        if passed:
            print(f"OK {case['id']} PASSED perfectly!")
        else:
            print("Full LLM Output:")
            pprint(result)

if __name__ == "__main__":
    asyncio.run(run_tests())
