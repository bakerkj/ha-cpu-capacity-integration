# Home Assistant CPU Capacity Integration

Custom integration that samples per-CPU load/frequency, computes rolling
averages (1m/5m/15m), and exposes sensors directly in Home Assistant.

## Features

- Internal sampling loop (default `0.5s`)
- Home Assistant entity updates at a slower interval (default `15s`)
- Per-CPU sensors for:
  - MHz averages (1m/5m/15m)
  - Load % averages (1m/5m/15m)
  - Capacity-Adjusted Load % averages (1m/5m/15m)
  - Max MHz
  - EPP
  - EPB

Capacity-Adjusted Load % formula:

`load_pct * (current_mhz / max_mhz)`

## Install

1. Copy `custom_components/cpu_capacity` into your Home Assistant
   `config/custom_components`.
2. Restart Home Assistant.
3. Add integration: **Settings -> Devices & Services -> Add Integration -> CPU
   Capacity**.

## Notes

- The integration reads only from `/proc` and `/sys/devices/system/cpu/*`.
- If some CPUs do not expose max MHz, capacity-adjusted sensors for those CPUs
  are omitted.
