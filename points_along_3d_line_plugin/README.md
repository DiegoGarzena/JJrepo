# Points Along 3D Line - QGIS Processing Plugin

A QGIS Processing algorithm designed to generate point vector layers along lines at fixed **3D spatial distances** taking into account actual terrain elevation (DTM).

---

## 💡 Background & Motivation

Standard GIS tools (like QGIS's native *Points along geometry*) calculate intervals strictly in **2D planar space**. In hilly or mountainous terrain, a 2D planar distance significantly underestimates the actual 3D ground distance traversed along the slope. 

This plugin was developed to bridge this gap by computing true 3D Euclidean distances ($\sqrt{\Delta x^2 + \Delta y^2 + \Delta z^2}$), ensuring precise spacing between generated points regardless of terrain slope and elevation changes.

---

## 🎯 Target Audience & Practical Use Cases

This tool is especially valuable for professionals working with complex topographies across various domain fields:

* **Geophysics & Exploration:** Planning geophone or sensor placement along seismic survey lines over rugged terrain where precise 3D surface intervals are critical.
* **Geotechnical & Geological Engineering:** Layout of sampling points, core drill holes, or slope monitoring station arrays.
* **Civil & Infrastructure Design:** Distributing structural supports, pipeline inspection points, or cable path anchors across elevation drops.
* **Hiking & Trail Planning:** Setting regular distance markers or waypoint stations along mountain tracks based on true walking/ground distance.

---

## ⚙️ Mandatory Workflow (Pre-Processing)

To achieve maximum accuracy and optimal execution speed, input geometries **must** follow a two-step pre-processing pipeline before running this algorithm:

1. **Densify by interval** (*Native QGIS Tool*)
   * Add intermediate vertices along the line layer (e.g., every 0.1m - 0.5m) so the line geometry closely conforms to the terrain profile.
2. **Drape / Set Z value from raster** (*Native QGIS Tool*)
   * Sample the underlying DTM raster to assign real elevation ($Z$ coordinates) directly to every vertex of the densified line.

---

## 🚀 Installation & Usage

1. Install via **QGIS Plugin Manager** (or load from ZIP).
2. Open **Processing Toolbox** $\rightarrow$ **Points Along 3D Line Tools** $\rightarrow$ **Points Along 3D Line**.
3. Select your pre-processed line layer, DTM raster, and target 3D distance interval (meters).
4. Run the algorithm to generate the output 3D Point layer (`PointZ`).

---

## 📄 License

Distributed under the **GNU General Public License v2.0 (GPL-2.0)**. See the `LICENSE` file for details.