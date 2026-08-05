import json
import random

cities = {
    "Chennai": {"lat": 13.0827, "lng": 80.2707, "count": 40},
    "Coimbatore": {"lat": 11.0168, "lng": 76.9558, "count": 25},
    "Madurai": {"lat": 9.9252, "lng": 78.1198, "count": 20},
    "Trichy": {"lat": 10.7905, "lng": 78.7047, "count": 15},
    "Salem": {"lat": 11.6643, "lng": 78.1460, "count": 15},
    "Erode": {"lat": 11.3410, "lng": 77.7172, "count": 10},
    "Tiruppur": {"lat": 11.1085, "lng": 77.3411, "count": 10},
    "Vellore": {"lat": 12.9165, "lng": 79.1325, "count": 10},
    "Thanjavur": {"lat": 10.7870, "lng": 79.1378, "count": 10},
    "Tirunelveli": {"lat": 8.7139, "lng": 77.7567, "count": 10},
    "Thoothukudi": {"lat": 8.7642, "lng": 78.1348, "count": 10},
    "Nagercoil": {"lat": 8.1833, "lng": 77.4119, "count": 10},
    "Dindigul": {"lat": 10.3673, "lng": 77.9803, "count": 5},
    "Kanchipuram": {"lat": 12.8342, "lng": 79.7036, "count": 5},
    "Ooty": {"lat": 11.4103, "lng": 76.7083, "count": 5}
}

prefixes = ["Anna Nagar", "Gandhi Road", "Main", "Bypass", "New Bus Stand", "Old Bus Stand", "Railway Station", "Highway", "Tollgate", "Cross", "Market", "Temple", "Collectorate"]

junctions = []
jid = 1

for city, data in cities.items():
    for _ in range(data["count"]):
        lat_offset = random.uniform(-0.05, 0.05)
        lng_offset = random.uniform(-0.05, 0.05)
        name = f"{random.choice(prefixes)} Signal, {city}"
        status = random.choice(["green", "amber", "red"])
        junctions.append({
            "id": jid,
            "name": name,
            "lat": round(data["lat"] + lat_offset, 4),
            "lng": round(data["lng"] + lng_offset, 4),
            "status": status,
            "wait_time": random.randint(10, 60),
            "vehicles": random.randint(0, 5)
        })
        jid += 1

# Patch main.py
with open("app/main.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if line.startswith("junctions = [") and start_idx == -1:
        start_idx = i
    elif start_idx != -1 and line.startswith("]"):
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    new_junctions_str = f"junctions = {json.dumps(junctions, indent=4)}\n"
    lines = lines[:start_idx] + [new_junctions_str] + lines[end_idx+1:]
    with open("app/main.py", "w", encoding="utf-8") as f:
        f.writelines(lines)
    print("Patched app/main.py successfully with 200 junctions!")

# Patch main.dart
dart_path = "../smart_traffic_app/lib/main.dart"
with open(dart_path, "r", encoding="utf-8") as f:
    dlines = f.readlines()

dstart = -1
dend = -1
for i, line in enumerate(dlines):
    if "static const List<Map<String, dynamic>> _allJunctionsFallback = [" in line:
        dstart = i
    elif dstart != -1 and "  ];" in line and i > dstart:
        dend = i
        break

if dstart != -1 and dend != -1:
    dart_list = "  static const List<Map<String, dynamic>> _allJunctionsFallback = [\n"
    for j in junctions:
        dart_list += f"    {json.dumps(j)},\n"
    dart_list += "  ];\n"
    
    dlines = dlines[:dstart] + [dart_list] + dlines[dend+1:]
    with open(dart_path, "w", encoding="utf-8") as f:
        f.writelines(dlines)
    print("Patched main.dart successfully with 200 junctions!")
