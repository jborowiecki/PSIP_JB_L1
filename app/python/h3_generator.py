"""Pełne komórki H3 ze środkiem wewnątrz powiatu."""
import argparse
import json
from pathlib import Path
import h3
from osgeo import ogr, osr


def read_area(path):
    source = ogr.Open(str(path))
    if source is None:
        raise ValueError(f"Nie można otworzyć: {path}")
    layer = source.GetLayer(0)
    reference = layer.GetSpatialRef()
    if reference is None:
        raise ValueError("Brak układu współrzędnych (.prj).")
    reference = reference.Clone()
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(reference, target)
    polygons = []
    for feature in layer:
        original = feature.GetGeometryRef()
        if original is None or original.IsEmpty():
            continue
        geometry = original.Clone()
        if geometry.Transform(transform) != 0 or not geometry.IsValid():
            raise ValueError("Niepoprawna geometria lub błąd transformacji.")
        geo = json.loads(geometry.ExportToJson())
        if geo["type"] == "Polygon":
            polygons.append(geo["coordinates"])
        elif geo["type"] == "MultiPolygon":
            polygons.extend(geo["coordinates"])
        else:
            raise ValueError("Obszar musi być poligonem lub multipoligonem.")
    if not polygons:
        raise ValueError("Brak geometrii powiatu.")
    return {"type": "MultiPolygon", "coordinates": polygons}


def generate_grid(area, resolution):
    if not 0 <= resolution <= 15:
        raise ValueError("Rozdzielczość H3 poza zakresem 0–15.")
    cells = sorted(h3.geo_to_cells(area, res=resolution))
    if not cells:
        raise ValueError(f"Brak komórek dla rozdzielczości {resolution}.")
    for cell in cells:
        ring = [[lon, lat] for lat, lon in h3.cell_to_boundary(cell)]
        ring.append(ring[0])
        yield {"type": "Feature", "properties": {"h3_id": cell, "resolution": resolution},
               "geometry": {"type": "Polygon", "coordinates": [ring]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="/data/powiat.shp")
    parser.add_argument("--resolutions", nargs="+", type=int, default=[7, 8, 9])
    parser.add_argument("--output-dir", default="/tmp/h3")
    args = parser.parse_args()
    area = read_area(args.input)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for res in sorted(set(args.resolutions)):
        features = list(generate_grid(area, res))
        (output / f"h3_r{res}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
        print(f"H3 {res}: {len(features)} komórek")


if __name__ == "__main__":
    main()
