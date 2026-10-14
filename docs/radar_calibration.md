# Radar colour scale: calibration against NEA gauges

**What:** data.gov.sg's `weather-radar-images` frames (product `dpsri`, surface rainfall intensity,
in dBR) draw rain intensity with 33 fixed colours. MSS's legend (weather.gov.sg, `rain-intensity.jpg`)
gives the colour **order** (light → heavy) but no numbers. The numbers are calibrated here against
NEA's 5-minute gauges.

**How** (`python -m floodsense.radar.calibrate`):
- **Pairing:** each archived 70 km frame (16 Jul to 30 Sep 2026, the overlap with the gauge store)
  is paired with every gauge's 5-minute reading at the same timestamp. The radar level is read at
  the gauge's pixel (about 290 m per pixel). That gives 1.8M pairs, 51,534 of them wet.
- **Lag:** zero lag correlates best (0.74, against 0.64–0.66 at ±5 min). The frame at *t* matches the
  gauge total for the 5 minutes ending at *t*.
- **Fit:** the gauge rate rises monotonically across all 33 levels, which confirms the legend order.
  Fit `log10(R) = −0.2063 + 0.06835·level` (0.68 dB per level) on levels 12–32. Levels 1–8 use the
  measured gauge mean, because the curve overestimates light rain there. One 0.2 mm tip in 5 minutes
  is already 2.4 mm/h.
- **Result:** `data/reference/radar_colour_scale.csv`, with level, RGB, rate, gauge mean, pairs and
  method.

**Agreement:**

| | 5-min corr | Hourly corr (per gauge) | Radar / gauge total | Heavy hours (≥20 mm), radar / gauge |
|---|---|---|---|---|
| Calibrated on Jul–Aug, tested on Sep | 0.83 | 0.92 | 1.03 | 0.78 (283 hours) |
| All data (Jul–Sep) | 0.81 | 0.92 | 0.99 | 0.79 (378 hours) |

**Limitations:**
- Radar reads about 20% low in the heaviest gauge hours. The beam and pixel average over ~300 m,
  while a gauge catches the core of a downpour.
- The scale covers rain rate only. Motion (nowcasting) is a separate step.

**Check on a known flood (30 Sep 2026):**
- **Woodlands** was the wettest zone at 15:00 (zone mean 106 mm/h), 13 minutes before its reported
  flood at 15:13.
- **Lim Chu Kang** was at 72 mm/h at 14:45, before its 15:38 report.
