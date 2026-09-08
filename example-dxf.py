import ezdxf

# Neues DXF-Dokument anlegen
doc = ezdxf.new("R2010")
msp = doc.modelspace()

# Layer für Platinenkontur (vom app.py Parser erwartet) und Bohrungen
doc.layers.add(name="OUTLINE", color=1)  # Rot
doc.layers.add(name="HOLES", color=3)    # Grün

# Platinenmaße in mm
width = 30.0
height = 50.0

# Geschlossene Außenkontur auf Layer 'OUTLINE'
outline_points = [
    (0.0, 0.0),
    (width, 0.0),
    (width, height),
    (0.0, height),
    (0.0, 0.0)
]
msp.add_lwpolyline(outline_points, dxfattribs={"layer": "OUTLINE"})

# M4 Durchgangslöcher (Durchmesser 4,5 mm -> Radius 2,25 mm)
# Randabstand: 4,0 mm zu den Kanten
margin = 4.0
hole_radius = 2.25

hole_centers = [
    (margin, margin),                  # Unten Links (4, 4)
    (width - margin, margin),          # Unten Rechts (26, 4)
    (width - margin, height - margin), # Oben Rechts (26, 46)
    (margin, height - margin)          # Oben Links (4, 46)
]

for center in hole_centers:
    msp.add_circle(center, radius=hole_radius, dxfattribs={"layer": "HOLES"})

# Speichern als board.dxf
doc.saveas("board.dxf")
print("board.dxf (30x50mm, 4x M4 Bohrungen) erfolgreich erzeugt.")
