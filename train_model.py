import numpy as np
import pandas as pd
from xgboost import XGBClassifier, XGBRegressor
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import accuracy_score, mean_absolute_error
import json
import pickle
import os

# ── Generate synthetic training data ─────────────────────────────────────────
def generate_training_data(n_samples=5000):
    np.random.seed(42)
    data = []
    for _ in range(n_samples):
        hour = np.random.randint(0, 24)
        day_of_week = np.random.randint(0, 7)
        junction_id = np.random.randint(1, 5)
        is_weekend = 1 if day_of_week >= 5 else 0
        is_peak_morning = 1 if 7 <= hour <= 9 else 0
        is_peak_evening = 1 if 16 <= hour <= 19 else 0
        weather = np.random.choice([0, 1, 2], p=[0.7, 0.2, 0.1])
        incident_nearby = np.random.choice([0, 1], p=[0.85, 0.15])

        base_vehicles = 200
        if is_peak_morning: base_vehicles += 600
        elif is_peak_evening: base_vehicles += 800
        elif 10 <= hour <= 15: base_vehicles += 300
        elif 0 <= hour <= 5: base_vehicles -= 150
        if is_weekend: base_vehicles -= 200
        if weather == 1: base_vehicles += 100
        if weather == 2: base_vehicles -= 50
        if incident_nearby: base_vehicles += 200
        base_vehicles += junction_id * 30
        vehicles = max(10, int(base_vehicles + np.random.normal(0, 50)))

        if vehicles > 700: congestion = 2
        elif vehicles > 400: congestion = 1
        else: congestion = 0

        if vehicles > 700: wait_time = np.random.randint(45, 90)
        elif vehicles > 400: wait_time = np.random.randint(20, 45)
        else: wait_time = np.random.randint(5, 20)

        if vehicles > 700: green_duration = 60
        elif vehicles > 400: green_duration = 45
        else: green_duration = 25

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
            'congestion_level': congestion,
            'wait_time': wait_time,
            'recommended_green_duration': green_duration,
        })
    return pd.DataFrame(data)

# ── Train congestion classifier ───────────────────────────────────────────────
def train_congestion_model(df):
    features = ['hour', 'day_of_week', 'junction_id', 'is_weekend',
                'is_peak_morning', 'is_peak_evening', 'weather', 'incident_nearby']
    X = df[features]
    y = df['congestion_level']

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = XGBClassifier(n_estimators=100, max_depth=6, learning_rate=0.1,
                          use_label_encoder=False, eval_metric='mlogloss', random_state=42)
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    print(f"Congestion model accuracy: {accuracy:.2%}")
    return model, features

# ── Train wait time regressor ─────────────────────────────────────────────────
def train_waittime_model(df):
    features = ['hour', 'day_of_week', 'junction_id', 'is_weekend',
                'is_peak_morning', 'is_peak_evening', 'weather', 'incident_nearby', 'vehicles']
    X = df[features]
    y = df['wait_time']

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = XGBRegressor(n_estimators=100, max_depth=6, learning_rate=0.1, random_state=42)
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    print(f"Wait time model MAE: {mae:.2f} seconds")
    return model, features

# ── Train signal timing recommender ──────────────────────────────────────────
def train_signal_model(df):
    features = ['hour', 'day_of_week', 'junction_id', 'is_weekend',
                'is_peak_morning', 'is_peak_evening', 'weather', 'incident_nearby']
    X = df[features]
    y = df['recommended_green_duration']

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = XGBRegressor(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42)
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    print(f"Signal timing model MAE: {mae:.2f} seconds")
    return model, features

# ── Prediction function ───────────────────────────────────────────────────────
def predict_traffic(congestion_model, waittime_model, signal_model,
                    congestion_features, waittime_features,
                    hour, day_of_week, junction_id, weather=0, incident_nearby=0, vehicles=300):
    is_weekend = 1 if day_of_week >= 5 else 0
    is_peak_morning = 1 if 7 <= hour <= 9 else 0
    is_peak_evening = 1 if 16 <= hour <= 19 else 0

    base_input = {
        'hour': hour, 'day_of_week': day_of_week, 'junction_id': junction_id,
        'is_weekend': is_weekend, 'is_peak_morning': is_peak_morning,
        'is_peak_evening': is_peak_evening, 'weather': weather,
        'incident_nearby': incident_nearby
    }

    congestion_df = pd.DataFrame([{f: base_input[f] for f in congestion_features}])
    congestion_level = int(congestion_model.predict(congestion_df)[0])
    congestion_proba = congestion_model.predict_proba(congestion_df)[0]

    waittime_input = {**base_input, 'vehicles': vehicles}
    waittime_df = pd.DataFrame([{f: waittime_input[f] for f in waittime_features}])
    predicted_wait = float(waittime_model.predict(waittime_df)[0])

    signal_df = pd.DataFrame([{f: base_input[f] for f in congestion_features}])
    recommended_green = float(signal_model.predict(signal_df)[0])

    congestion_labels = {0: 'Low', 1: 'Medium', 2: 'High'}
    congestion_colors = {0: 'green', 1: 'amber', 2: 'red'}

    return {
        'junction_id': junction_id,
        'hour': hour,
        'congestion_level': congestion_level,
        'congestion_label': congestion_labels[congestion_level],
        'congestion_color': congestion_colors[congestion_level],
        'confidence': float(max(congestion_proba)),
        'predicted_wait_time_seconds': round(predicted_wait),
        'recommended_green_duration_seconds': round(recommended_green),
        'is_peak_hour': bool(is_peak_morning or is_peak_evening),
        'prediction_factors': {
            'is_weekend': bool(is_weekend),
            'is_peak_morning': bool(is_peak_morning),
            'is_peak_evening': bool(is_peak_evening),
            'weather_impact': ['None', 'Rain', 'Heavy Rain'][weather],
            'incident_nearby': bool(incident_nearby),
        }
    }

# ── Main training script ──────────────────────────────────────────────────────
if __name__ == '__main__':
    print("Generating training data...")
    df = generate_training_data(5000)
    print(f"Generated {len(df)} samples")
    print(f"Congestion distribution:\n{df['congestion_level'].value_counts()}\n")

    print("Training congestion classifier...")
    congestion_model, congestion_features = train_congestion_model(df)

    print("\nTraining wait time predictor...")
    waittime_model, waittime_features = train_waittime_model(df)

    print("\nTraining signal timing recommender...")
    signal_model, signal_features = train_signal_model(df)

    os.makedirs('models', exist_ok=True)
    with open('models/congestion_model.pkl', 'wb') as f:
        pickle.dump({'model': congestion_model, 'features': congestion_features}, f)
    with open('models/waittime_model.pkl', 'wb') as f:
        pickle.dump({'model': waittime_model, 'features': waittime_features}, f)
    with open('models/signal_model.pkl', 'wb') as f:
        pickle.dump({'model': signal_model, 'features': signal_features}, f)

    print("\nModels saved to models/ folder!")
    print("\nTesting prediction...")

    test_result = predict_traffic(
        congestion_model, waittime_model, signal_model,
        congestion_features, waittime_features,
        hour=17, day_of_week=1, junction_id=1,
        weather=0, incident_nearby=0, vehicles=750
    )
    print("\nSample prediction (5 PM, Monday, Junction 1):")
    print(json.dumps(test_result, indent=2))
    print("\nAll models trained and saved successfully!")
