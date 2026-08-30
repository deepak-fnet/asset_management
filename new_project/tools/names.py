"""Deterministic generator of realistic industrial product names."""
MATERIALS = ["Stainless Steel", "Carbon Steel", "Brass", "Aluminium", "Copper",
             "Galvanised Iron", "Cast Iron", "Titanium", "Nylon", "PVC",
             "PTFE", "Bronze", "Mild Steel", "Alloy Steel", "Ductile Iron",
             "Polypropylene", "Inconel", "Monel", "Zinc Plated", "Chrome Plated"]
ITEMS = ["Hex Bolt", "Hex Nut", "Flat Washer", "Spring Washer", "Socket Screw",
         "Grub Screw", "Anchor Bolt", "Threaded Rod", "Ball Bearing", "Roller Bearing",
         "Thrust Bearing", "Oil Seal", "O-Ring", "Gasket Sheet", "Butterfly Valve",
         "Gate Valve", "Globe Valve", "Check Valve", "Ball Valve", "Needle Valve",
         "Elbow Fitting", "Tee Fitting", "Reducer Coupling", "Pipe Nipple", "Blind Flange",
         "Weld Neck Flange", "Slip On Flange", "Compression Fitting", "Hose Clamp", "Cable Gland",
         "Circuit Breaker", "Contactor Relay", "Terminal Block", "Junction Box", "Cable Tray",
         "Control Panel", "Pressure Gauge", "Temperature Sensor", "Flow Meter", "Level Switch",
         "Centrifugal Pump", "Gear Pump", "Air Compressor", "Drive Belt", "Timing Chain",
         "Sprocket Wheel", "Gear Coupling", "Shaft Sleeve", "Bushing", "Lifting Eye Bolt"]
SIZES = ["M4", "M5", "M6", "M8", "M10", "M12", "M14", "M16", "M18", "M20",
         "M22", "M24", "1/4 in", "3/8 in", "1/2 in", "3/4 in", "1 in", "1.5 in",
         "2 in", "2.5 in", "3 in", "4 in", "6 in", "8 in", "10 in"]
GRADES = ["Grade 4.6", "Grade 8.8", "Grade 10.9", "Grade 12.9", "Class 150",
          "Class 300", "Class 600", "PN10", "PN16", "PN25", "Type A", "Type B",
          "Series 100", "Series 200", "Series 300", "Heavy Duty", "Light Duty",
          "Standard Duty", "High Pressure", "Food Grade"]


def product_names(count, offset=0):
    """Yield `count` unique, human-readable product names starting at `offset`."""
    nm, ni, ns, ng = len(MATERIALS), len(ITEMS), len(SIZES), len(GRADES)
    total = nm * ni * ns * ng
    for k in range(offset, offset + count):
        i = k % total
        g = i % ng
        i //= ng
        s = i % ns
        i //= ns
        it = i % ni
        i //= ni
        m = i % nm
        yield "%s %s %s %s" % (MATERIALS[m], ITEMS[it], SIZES[s], GRADES[g])
