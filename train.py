import pandas as pd
import numpy as np
import pickle
import os
from datetime import datetime, timedelta
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, mean_absolute_error

# ── Create models folder if not exists ────────────────────────
os.makedirs('models', exist_ok=True)

print("=" * 50)
print("  Smart Traffic AI - Model Training")
print("=" * 50)

# ── Generate Synthetic Training Data ──────────────────────────
print("\n[1/4] Generating training data...")

np.random.seed(42)
n_samples = 5000

data = []
for _ in range(n_samples):
    hour = np.random.randint(0, 24)
    day_of_week = np.random.randint(0, 7)
    junction_id = np.random.randint(1, 5)
    is_weekend = 1 if day_of_week >= 5 else 0
    is_peak_morning = 1 if 7 <= hour <= 9 else 0
    is_peak_evening = 1 if 16 <= hour <= 19 else 0
    weather = np.random.randint(0, 3)         # 0=clear, 1=rain, 2=fog
    incident_nearby = np.random.randint(0, 2) # 0=no, 1=yes
    vehicles = np.random.randint(5, 100)

    # Congestion logic
    score = 0
    if is_peak_morning or is_peak_evening:
        score += 2
    if weather > 0:
        score += 1
    if incident_nearby:
        score += 2
    if vehicles > 60:
        score += 1
    if is_weekend:
        score -= 1

    if score <= 1:
        congestion = 0  # Low
    elif score <= 3:
        congestion = 1  # Medium
    else:
        congestion = 2  # High

    # Wait time logic (seconds)
    base_wait = vehicles * 0.8
    if is_peak_morning or is_peak_evening:
        base_wait *= 1.5
    if weather > 0:
        base_wait *= 1.2
    if incident_nearby:
        base_wait *= 1.4
    wait_time = max(10, base_wait + np.random.normal(0, 5))

    # Recommended green signal (seconds)
    if congestion == 2:
        green_time = np.random.randint(45, 75)
    elif congestion == 1:
        green_time = np.random.randint(25, 45)
    else:
        green_time = np.random.randint(15, 30)

    data.append({
        'hour': hour,
        'day_of_week': day_of_week,
        'junction_id': junction_id,
        'is_weekend': is_weekend,
        'is_peak_morning': is_peak_morning,
        'is_peak_evening': is_peak_evening,
        'weather': weather,
        'incident_nearby': incident_nearby,
        'vehicles': vehicles,
        'congestion': congestion,
        'wait_time': round(wait_time, 1),
        'green_time': green_time,
    })

df = pd.DataFrame(data)
print(f"   Generated {len(df)} samples ✓")

# ── Train Congestion Model ─────────────────────────────────────
print("\n[2/4] Training Congestion Model...")

congestion_features = ['hour', 'day_of_week', 'junction_id', 'is_weekend',
                        'is_peak_morning', 'is_peak_evening', 'weather', 'incident_nearby']

X = df[congestion_features]
y = df['congestion']

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

congestion_model = RandomForestClassifier(n_estimators=100, random_state=42)
congestion_model.fit(X_train, y_train)

accuracy = accuracy_score(y_test, congestion_model.predict(X_test))
print(f"   Congestion Model Accuracy: {accuracy * 100:.1f}% ✓")

with open('models/congestion_model.pkl', 'wb') as f:
    pickle.dump({'model': congestion_model, 'features': congestion_features}, f)
print("   Saved: models/congestion_model.pkl ✓")

# ── Train Wait Time Model ──────────────────────────────────────
print("\n[3/4] Training Wait Time Model...")

waittime_features = ['hour', 'day_of_week', 'junction_id', 'is_weekend',
                     'is_peak_morning', 'is_peak_evening', 'weather', 'incident_nearby', 'vehicles']

X2 = df[waittime_features]
y2 = df['wait_time']

X2_train, X2_test, y2_train, y2_test = train_test_split(X2, y2, test_size=0.2, random_state=42)

waittime_model = RandomForestRegressor(n_estimators=100, random_state=42)
waittime_model.fit(X2_train, y2_train)

mae = mean_absolute_error(y2_test, waittime_model.predict(X2_test))
print(f"   Wait Time Model MAE: {mae:.1f} seconds ✓")

with open('models/waittime_model.pkl', 'wb') as f:
    pickle.dump({'model': waittime_model, 'features': waittime_features}, f)
print("   Saved: models/waittime_model.pkl ✓")

# ── Train Signal Timing Model ──────────────────────────────────
print("\n[4/4] Training Signal Timing Model...")

signal_features = ['hour', 'day_of_week', 'junction_id', 'is_weekend',
                   'is_peak_morning', 'is_peak_evening', 'weather', 'incident_nearby']

X3 = df[signal_features]
y3 = df['green_time']

X3_train, X3_test, y3_train, y3_test = train_test_split(X3, y3, test_size=0.2, random_state=42)

signal_model = RandomForestRegressor(n_estimators=100, random_state=42)
signal_model.fit(X3_train, y3_train)

mae3 = mean_absolute_error(y3_test, signal_model.predict(X3_test))
print(f"   Signal Model MAE: {mae3:.1f} seconds ✓")

with open('models/signal_model.pkl', 'wb') as f:
    pickle.dump({'model': signal_model, 'features': signal_features}, f)
print("   Saved: models/signal_model.pkl ✓")

# ── Done ───────────────────────────────────────────────────────
print("\n" + "=" * 50)
print("  All 3 models trained and saved successfully!")
print("  You can now run main.py")
print("=" * 50)
