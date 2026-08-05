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
        wait_time = random.randint(10, 60)
        vehicles = random.randint(0, 5)
        
        junctions.append({
            "id": jid,
            "name": name,
            "lat": round(data["lat"] + lat_offset, 4),
            "lng": round(data["lng"] + lng_offset, 4),
            "status": status,
            "wait_time": wait_time,
            "vehicles": vehicles
        })
        jid += 1

print(f"junctions = {json.dumps(junctions, indent=4)}")
