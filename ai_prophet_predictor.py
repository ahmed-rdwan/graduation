import pandas as pd
from prophet import Prophet
from pymongo import MongoClient
from datetime import datetime, timedelta
import logging

import os
from dotenv import load_dotenv

# قفل رسايل التحذير المزعجة عشان الـ Console يبقى نضيف
logging.getLogger("prophet").setLevel(logging.WARNING)
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

# الاتصال بالداتا بيز

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI"))
db = client["test"]

# إعدادات الموديل
MIN_HISTORY_RECORDS = 5   # الحد الأدنى للسجلات عشان Prophet يتدرب
FORECAST_DAYS = 14        # كام يوم قدام هيتوقع
ALERT_THRESHOLD_DAYS = 10 # لو المخزن هيخلص في الفترة دي، نفتح تيكت


def prepare_daily_dataframe(history):
    """
    Converts MongoDB records into a Prophet-ready DataFrame.
    Aggregates withdrawn quantities per day into a single row.
    """
    df = pd.DataFrame(history)

    # تحويل التاريخ لـ يوم بس (بدون ساعات) عشان نجمع السحب اليومي
    date_col = "timestamp" if "timestamp" in df.columns else "transaction_date"
    df["ds"] = pd.to_datetime(df[date_col]).dt.normalize()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)

    # تجميع إجمالي السحب لكل يوم في صف واحد
    daily_df = df.groupby("ds")["quantity"].sum().reset_index()
    daily_df.rename(columns={"quantity": "y"}, inplace=True)

    return daily_df


def train_and_forecast(daily_df):
    """
    Builds, trains a Prophet model and returns the forecast.
    Returns None if data is insufficient (less than 2 distinct dates).
    """
    # Prophet محتاج على الأقل يومين مختلفين عشان ما يـ crash مش
    if daily_df["ds"].nunique() < 2:
        return None

    model = Prophet(
        yearly_seasonality=False,  # مش محتاجين سنوي، داتاتنا 90 يوم بس
        weekly_seasonality=True,   # مهم عشان يفهم باترن أيام الإجازات
        daily_seasonality=False
    )
    model.fit(daily_df)

    future = model.make_future_dataframe(periods=FORECAST_DAYS)
    forecast = model.predict(future)

    return forecast


def create_alert_ticket(item_name, current_qty, days_left, empty_date):
    """
    Opens an emergency ticket if stock is about to run out.
    Checks first that no open ticket exists for the same item to prevent duplicates.
    """
    ticket_title = f"AI Stock Alert: {item_name}"

    # حماية من التكرار: لو في تيكت مفتوح بالفعل، نتخطى
    existing = db.tickets.find_one({"name": ticket_title, "status": "Open"})
    if existing:
        print(f"   ⏳ An open alert ticket already exists for this item. Skipping.")
        return

    admin = db.users.find_one({"type": "admin"})
    admin_id = admin["_id"] if admin else None

    db.tickets.insert_one({
        "name": ticket_title,
        "description": (
            f"AI System Warning: Current stock for '{item_name}' is ({current_qty}) units. "
            f"Based on the predicted consumption rate, stock is expected to run out in {days_left} days "
            f"(by {empty_date.strftime('%Y-%m-%d')}). Please contact suppliers immediately."
        ),
        "priority": "High",
        "status": "Open",
        "created_by": admin_id,
        "created_at": datetime.utcnow()
    })
    print(f"   🎟️ Emergency ticket created automatically.")


def analyze_stock_item(stock):
    """
    Analyzes a single stock item:
    1. Fetches consumption history from ai_stock_history
    2. Trains a Prophet model
    3. Calculates when stock will run out
    4. Opens an alert ticket if the situation is critical
    """
    item_id = stock["_id"]
    item_name = stock["name"]
    current_qty = stock["quantity"]

    print(f"\n{'='*40}")
    print(f"📦 Analyzing: '{item_name}' | Current stock: {current_qty}")

    # مخزن فارغ من الأساس، مفيش داعي للتحليل
    if current_qty <= 0:
        print(f"   ⚠️ Stock is already empty.")
        return {"item_id": str(item_id), "item_name": item_name, "status": "empty", "current_qty": 0}

    # سحب سجلات الاستهلاك (remove بس، الـ add مش بيأثر على التوقع)
    history = list(db.ai_stock_history.find({"stock_id": item_id, "action": "remove"}))

    if len(history) < MIN_HISTORY_RECORDS:
        print(f"   📊 Insufficient data for training ({len(history)}/{MIN_HISTORY_RECORDS} records).")
        return {"item_id": str(item_id), "item_name": item_name, "status": "insufficient_data", "current_qty": current_qty}

    print(f"   🧠 Training Meta Prophet model on {len(history)} records...")

    daily_df = prepare_daily_dataframe(history)
    forecast = train_and_forecast(daily_df)

    # لو الداتا كلها في نفس اليوم، Prophet مش هيشتغل
    if forecast is None:
        print(f"   ⚠️ All records share the same date. Prophet requires variation across dates.")
        return {"item_id": str(item_id), "item_name": item_name, "status": "insufficient_variation", "current_qty": current_qty}

    # حساب متوسط السحب اليومي المتوقع في الـ FORECAST_DAYS الجايين
    # الـ yhat في Prophet = الرقم المتوقع
    predicted_daily_burn = forecast.tail(FORECAST_DAYS)["yhat"].mean()

    # لو التوقع طلع سالب أو صفر، نحط حد أدنى منطقي عشان نتجنب القسمة على صفر
    daily_burn_rate = max(0.1, predicted_daily_burn)

    # حساب امتى المخزن هيخلص بناءً على معدل السحب
    days_left = int(current_qty / daily_burn_rate)
    empty_date = datetime.utcnow() + timedelta(days=days_left)

    print(f"   ➤ Predicted daily burn rate: {round(daily_burn_rate, 2)} units/day")
    print(f"   ➤ Stock expected to run out in: {days_left} days (by {empty_date.strftime('%Y-%m-%d')})")

    # نظام التحذير: لو المخزن هيخلص في الـ ALERT_THRESHOLD_DAYS الجايين
    if days_left <= ALERT_THRESHOLD_DAYS:
        print(f"   🚨 Critical warning: stock is about to run out!")
        create_alert_ticket(item_name, current_qty, days_left, empty_date)
    else:
        print(f"   ✅ Stock level is safe.")
        
    return {
        "item_id": str(item_id),
        "item_name": item_name,
        "current_qty": current_qty,
        "status": "critical" if days_left <= ALERT_THRESHOLD_DAYS else "safe",
        "daily_burn_rate": round(daily_burn_rate, 2),
        "days_left": days_left,
        "empty_date": empty_date.isoformat()
    }


from bson import ObjectId

def predict_stock_with_meta(company_id: str = None):
    print("🤖 Meta Prophet AI Core Started...\n" + "="*40)

    query = {}
    if company_id:
        query["company_id"] = ObjectId(company_id) if len(company_id) == 24 else company_id

    stocks = list(db.stocks.find(query))

    if not stocks:
        print("❌ No stock items found in the database.")
        return

    results = []
    for stock in stocks:
        res = analyze_stock_item(stock)
        if res:
            results.append(res)

    print(f"\n{'='*40}")
    print("🏁 Prediction cycle completed successfully.")
    return results


if __name__ == "__main__":
    predict_stock_with_meta()