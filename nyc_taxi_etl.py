import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import create_engine
import time

print("🚀 Starting NYC Taxi ETL Pipeline...")
start_time = time.time()

# ==========================================
# 1. LOAD DATA (Using fastparquet to bypass PyArrow memory limits)
# ==========================================
print("📥 Reading first 100,000 rows using fastparquet...")

# Only load the columns we need (Note: 'Airport_fee' has a capital 'A'!)
columns_to_load = [
    'VendorID', 'tpep_pickup_datetime', 'tpep_dropoff_datetime',
    'passenger_count', 'trip_distance', 'RatecodeID',
    'PULocationID', 'DOLocationID', 'payment_type',
    'fare_amount', 'extra', 'mta_tax', 'tip_amount',
    'tolls_amount', 'improvement_surcharge', 'total_amount',
    'congestion_surcharge', 'Airport_fee', 'cbd_congestion_fee'
]

#  THE FIX: Use engine='fastparquet' instead of 'pyarrow'
df = pd.read_parquet('data/yellow_tripdata.parquet', columns=columns_to_load, engine='fastparquet')

# Slice to 100,000 rows immediately
df = df.head(100000)

print(f"✅ Successfully loaded {len(df):,} rows into memory.")


# ==========================================
# 2. CLEAN & TRANSFORM
# ==========================================
print("🧹 Cleaning data and building Star Schema...")

# Convert datetime columns to proper datetime objects
df['tpep_pickup_datetime'] = pd.to_datetime(df['tpep_pickup_datetime'])
df['tpep_dropoff_datetime'] = pd.to_datetime(df['tpep_dropoff_datetime'])

# Filter out invalid trips (negative fares, zero distance, or null datetimes)
df = df[(df['fare_amount'] > 0) & (df['trip_distance'] > 0)]
df = df.dropna(subset=['tpep_pickup_datetime', 'tpep_dropoff_datetime'])

# --- Create Dimension Tables ---

# 1. dim_datetime
dim_datetime = df[['tpep_pickup_datetime']].drop_duplicates().copy()
dim_datetime['datetime_id'] = dim_datetime['tpep_pickup_datetime'].dt.strftime('%Y%m%d%H%M%S')
dim_datetime['year'] = dim_datetime['tpep_pickup_datetime'].dt.year
dim_datetime['month'] = dim_datetime['tpep_pickup_datetime'].dt.month
dim_datetime['day'] = dim_datetime['tpep_pickup_datetime'].dt.day
dim_datetime['hour'] = dim_datetime['tpep_pickup_datetime'].dt.hour
dim_datetime['day_of_week'] = dim_datetime['tpep_pickup_datetime'].dt.day_name()

# 2. dim_payment_type
dim_payment = pd.DataFrame({
    'payment_type': [0, 1, 2, 3, 4, 5, 6],
    'payment_description': [
        'Flex Fare', 'Credit Card', 'Cash', 
        'No Charge', 'Dispute', 'Unknown', 'Voided Trip'
    ]
})

# 3. dim_vendor
dim_vendor = pd.DataFrame({
    'vendor_id': [1, 2, 6, 7],
    'vendor_name': [
        'Creative Mobile Technologies', 'Curb Mobility', 
        'Myle Technologies', 'Helix'
    ]
})

# 4. dim_ratecode
dim_ratecode = pd.DataFrame({
    'ratecode_id': [1, 2, 3, 4, 5, 6, 99],
    'ratecode_description': [
        'Standard rate', 'JFK', 'Newark', 
        'Nassau or Westchester', 'Negotiated fare', 'Group ride', 'Null/unknown'
    ]
})

# 5. dim_location (Just unique IDs for now)
dim_pickup = pd.DataFrame({'pickup_location_id': df['PULocationID'].unique()})
dim_dropoff = pd.DataFrame({'dropoff_location_id': df['DOLocationID'].unique()})

# --- Create Fact Table ---
fact_trips = df.copy()

# Map datetime_id to fact table
fact_trips = fact_trips.merge(dim_datetime[['tpep_pickup_datetime', 'datetime_id']], on='tpep_pickup_datetime', how='left')

# Keep only necessary columns for the fact table
fact_cols = [
    'datetime_id', 'VendorID', 'PULocationID', 'DOLocationID', 
    'payment_type', 'passenger_count', 'trip_distance', 'RatecodeID', 
    'fare_amount', 'extra', 'mta_tax', 'tip_amount', 'tolls_amount', 
    'improvement_surcharge', 'total_amount', 'congestion_surcharge', 
    'Airport_fee', 'cbd_congestion_fee'
]

# Filter the list to only include columns that actually exist in the file
fact_cols = [col for col in fact_cols if col in fact_trips.columns]
fact_trips = fact_trips[fact_cols]

# ==========================================
# 3. LOAD TO POSTGRESQL
# ==========================================
print("📤 Connecting to PostgreSQL and loading tables...")

# IMPORTANT: CHANGE 'YOUR_PASSWORD' TO YOUR ACTUAL POSTGRES PASSWORD!
# Added isolation_level="AUTOCOMMIT" to force the database to save immediately
engine = create_engine(
    'postgresql+psycopg2://postgres:YOURPASSOWRD@localhost:5433/DataWareHouse',
    isolation_level="AUTOCOMMIT"
)

# Create schema
from sqlalchemy import text
with engine.begin() as conn:
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS nyc_taxi"))
print("✅ Schema 'nyc_taxi' is ready.")

tables = {
    'nyc_taxi.dim_datetime': dim_datetime,
    'nyc_taxi.dim_payment_type': dim_payment,
    'nyc_taxi.dim_vendor': dim_vendor,
    'nyc_taxi.dim_ratecode': dim_ratecode,
    'nyc_taxi.dim_pickup_location': dim_pickup,
    'nyc_taxi.dim_dropoff_location': dim_dropoff,
    'nyc_taxi.fact_trips': fact_trips
}

for table_name, table_df in tables.items():
    schema, table = table_name.split('.')
    print(f"   -> Loading {table_name} ({len(table_df):,} rows)...")
    try:
        table_df.to_sql(table, engine, schema=schema, if_exists='replace', index=False)
        print(f"      ✅ Success!")
    except Exception as e:
        print(f"       FAILED: {e}")
        break

print("🎉 Pipeline execution finished!")