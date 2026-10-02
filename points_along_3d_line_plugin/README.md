# Points Along 3D Line - QGIS Processing Plugin

A QGIS Processing algorithm that generates points along lines at a constant **3D distance**, measured on the terrain surface, using a DTM raster for the elevation.

---

## Features

- Sampling at a true 3D (slope) interval along line geometries.
- **No pre-processing required**: lines are densified and draped on the DTM internally, with bilinear interpolation.
- The DTM may have a different CRS from the lines; the band can be selected.
- Optional start offset and optional end point.
- Clear warning when a line goes beyond the edge of the DTM, with an optional flat extension of the last valid elevation.
- Works with single and multipart lines, with selected features only, and with temporary layers.
- Output is a `PointZ` layer with configurable attributes:

| Field | Description |
|---|---|
| `line_id` | Value of the chosen ID field, or the custom name/prefix, or `Line_N`. |
| `part_id` | Part number of multipart lines (`seq_id` restarts for every part). |
| `seq_id` | Point number along the line (part), starting at 1. |
| `x_coord`, `y_coord` | Coordinates in any selected CRS. |
| `z_ele` | Elevation interpolated from the DTM. |
| `z_extrap` | Only with *Keep points beyond the DTM edge*: 1 for points placed beyond the DTM edge with an assumed elevation, 0 for points with a real DTM elevation. |
| `dist_2d_p`, `dist_2d_tot` | Horizontal distance from the previous point / from the start of the line, measured along the path. |
| `dist_3d_p`, `dist_3d_tot` | 3D distance from the previous point / from the start of the line, measured along the draped line (equal to the interval between regular points). |
| `delta_z_p` | Elevation difference from the previous point. |
| `slope_deg`, `slope_pct` | Slope of the step from the previous point, **signed**: positive uphill, negative downhill. Empty for the first point. |
| `azimuth_deg` | Grid bearing (0-360, clockwise from the +Y axis of the input CRS) of the step from the previous point; for the first point, towards the next one. |

---

## Background & Motivation

Standard GIS tools (like the native *Points along geometry*) measure intervals in **2D planar space**. In hilly or mountainous terrain this significantly underestimates the real distance walked along the slope.

This plugin measures the interval along the **3D polyline** of the line draped on the terrain, i.e. the sum of the segment lengths $\sqrt{\Delta x^2 + \Delta y^2 + \Delta z^2}$, so that the spacing between points follows the actual ground surface regardless of slope.

---

## Target Audience & Practical Use Cases

* **Geophysics & Exploration:** planning geophone or sensor placement along seismic survey lines over rugged terrain, where surface intervals are critical.
* **Geotechnical & Geological Engineering:** layout of sampling points, core drill holes or slope monitoring stations.
* **Civil & Infrastructure Design:** distributing supports, pipeline inspection points or cable anchors across elevation drops.
* **Hiking & Trail Planning:** regular distance markers or waypoints along mountain tracks, based on real ground distance.

---

## How it works

1. Each line (or line part) is **densified** internally (see below).
2. Every densified vertex gets its elevation from the DTM with **bilinear interpolation**. Nearest-neighbour sampling is deliberately avoided: on a densified line it produces a staircase profile whose 3D length is overestimated.
3. The resulting 3D polyline is walked from its start and a point is created every time the cumulative 3D length reaches the chosen interval.

Any Z values already stored in the input geometries are ignored: the elevation always comes from the DTM.

### Why densification matters

A DTM is a grid of cells, each with its own elevation, but a line may have only two vertices over hundreds of metres: from its geometry alone the tool would not know that the ground goes up and down in between. **Densification** adds intermediate vertices along the line (without moving it). The DTM elevation is read at each of them and the 3D distance is measured along this polyline, which follows the ground.

The **densification step** is the distance between these vertices:

- a **smaller step** follows the relief more faithfully, but takes longer;
- a step **larger than the DTM pixel** skips the terrain between vertices and **underestimates the 3D length**, so points end up too far apart on the ground;
- a step **much smaller than the pixel** adds almost nothing, because the DTM has no more detail than that.

The default (**0 = automatic**) is half of the DTM pixel size, which is a good choice in nearly all cases. Change it only for special needs, such as a very fast preview (larger step) or an exceptionally detailed check (smaller step).

### Lines extending beyond the DTM

If part of a line lies outside the DTM (or on NoData) the algorithm shows a **red warning** in the log, listing the affected lines and the percentage of the sampling vertices without elevation.

- **Default:** points are created only where the DTM has a value, so the line is cut at the edge. Lines completely outside the DTM produce no points (also reported in red).
- **Keep points beyond the DTM edge** (unchecked by default): points are also created beyond the edge, with a constant elevation equal to that of the nearest valid vertex, as if the ground were flat there. These points are flagged with `z_extrap = 1` so they can be told apart (and filtered out) later.
- Gaps in the *middle* of a line are always bridged by linear interpolation of the elevation. Missing values are never replaced with elevation 0.

---

## Installation & Usage

1. Download the latest release `.zip`.
2. In QGIS, go to `Plugins > Manage and Install Plugins... > Install from ZIP`.
3. Open the **Processing Toolbox** → **Points Along 3D Line Tools** → **3D Vector Tools** → **Points Along 3D Line**.
4. Select the line layer (projected CRS), the DTM raster and the 3D distance interval.
5. Run the algorithm to generate the 3D point layer (`PointZ`).

### Parameters

| Parameter | Notes |
|---|---|
| Input line layer | Line or multiline features, in a **projected** CRS (geographic CRS are rejected). |
| DTM raster, band | Elevation source. Its elevation unit must be the same as the horizontal unit of the line layer (normally metres). |
| 3D distance interval | Distance between consecutive points along the draped line. |
| Start offset | 3D distance from the start of each line at which the first point is placed (default 0). |
| Include end point | Also adds the last vertex of the line when it is not on a regular interval. |
| Keep points beyond the DTM edge | Unchecked by default. Creates points also where the line goes beyond the DTM, with the elevation of the nearest valid vertex (see above). Adds the `z_extrap` field. |
| Line ID field / custom ID | Source of `line_id`. |
| CRS for x_coord / y_coord | Defaults to the project CRS. The point geometries keep the CRS of the input lines. |
| Densification step *(advanced)* | Distance between the sampling vertices; 0 = automatic (half DTM pixel). See *Why densification matters*. |
| Include ... *(advanced)* | Checkboxes to choose which attributes are written. |

---

## Notes and limitations

- Distances are path lengths along the draped line, not straight chords between points.
- Without *Keep points beyond the DTM edge*, distances and the start offset are measured from the first vertex that has a valid elevation, not from the geometric start of a line that begins outside the DTM.
- The accuracy of the result is bounded by the DTM resolution and quality.
- If you save the output as an ESRI Shapefile, field names longer than 10 characters (`dist_2d_tot`, `dist_3d_tot`, `azimuth_deg`) are truncated. GeoPackage is recommended.

---

## Development

The geometric logic is in `core.py` and does not depend on QGIS, so it can be tested with a plain Python interpreter:

```
python -m unittest discover tests
```

---

## License

Distributed under the **GNU General Public License v2.0 (GPL-2.0)**. See the `LICENSE` file for details.
