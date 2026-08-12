"""
models/reports.py - כל חישובי הדשבורד: כמות תורים השבוע, תורים שהושלמו בשבוע שעבר,
הכנסות שבועיות והכנסות חודשיות. כל הפונקציות כאן הן "קריאה בלבד" - הן לא משנות נתונים.
"""

import sys                                  # מודול מערכת, כדי שנוכל לגשת לתיקיית הפרויקט הראשית
import os                                   # מודול נתיבי קבצים
import datetime                             # מודול לעבודה עם תאריכים וחישובי טווחי זמן
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # מוסיף את תיקיית הפרויקט ל-path

import database  # קובץ הגישה לבסיס הנתונים


def _week_range(week_offset=0):
    """מחשב את טווח השבוע (ראשון עד שבת) הנוכחי, או שבוע קודם/הבא לפי week_offset. מחזיר (start, end) כמחרוזות."""
    today = datetime.date.today()                             # תאריך היום הנוכחי
    days_since_sunday = (today.weekday() + 1) % 7                # מספר הימים שחלפו מיום ראשון האחרון (ראשון=0)
    this_sunday = today - datetime.timedelta(days=days_since_sunday)  # תאריך יום ראשון של השבוע הנוכחי
    start = this_sunday + datetime.timedelta(weeks=week_offset)    # תחילת השבוע המבוקש (עם היסט אם התבקש)
    end = start + datetime.timedelta(days=6)                          # סוף השבוע המבוקש (שבת)
    return start.isoformat(), end.isoformat()                          # החזרת שני התאריכים כמחרוזות YYYY-MM-DD


def get_current_week_appointments():
    """מחזיר את רשימת כל התורים (הפעילים, שלא בוטלו) בשבוע הנוכחי, כולל שמות השירותים בכל תור,
    ממוינים לפי תאריך ושעה."""
    start, end = _week_range(0)                                  # חישוב טווח השבוע הנוכחי
    connection = database.get_connection()                        # פתיחת חיבור לבסיס הנתונים
    rows = connection.execute(                                       # שליפת כל התורים הפעילים בטווח השבוע
        "SELECT appointments.*, customers.full_name AS customer_name, "
        "GROUP_CONCAT(services.name, ', ') AS service_names "
        "FROM appointments "
        "LEFT JOIN customers ON appointments.customer_id = customers.id "
        "LEFT JOIN appointment_services ON appointment_services.appointment_id = appointments.id "
        "LEFT JOIN services ON appointment_services.service_id = services.id "
        "WHERE appointment_date BETWEEN ? AND ? AND status != 'בוטל' "
        "GROUP BY appointments.id "
        "ORDER BY appointment_date, appointment_time",
        (start, end)
    ).fetchall()
    connection.close()                                               # סגירת החיבור
    return [dict(row) for row in rows]                                 # המרת התוצאות לרשימת מילונים


def get_last_week_completed_count():
    """מחזיר את מספר התורים שסומנו כ'בוצע' במהלך השבוע הקודם (ראשון עד שבת שעבר)."""
    start, end = _week_range(-1)                                   # חישוב טווח השבוע הקודם
    connection = database.get_connection()                          # פתיחת חיבור לבסיס הנתונים
    row = connection.execute(                                          # ספירת התורים שהושלמו בטווח זה
        "SELECT COUNT(*) AS total FROM appointments "
        "WHERE appointment_date BETWEEN ? AND ? AND status = 'בוצע'",
        (start, end)
    ).fetchone()
    connection.close()                                                 # סגירת החיבור
    return row["total"]                                                  # החזרת מספר התורים שהושלמו


def get_weekly_revenue(week_offset=0):
    """מחזיר את סך ההכנסה (סכום המחירים) מתורים שהושלמו בשבוע מבוקש (0=נוכחי, -1=קודם וכו')."""
    start, end = _week_range(week_offset)                          # חישוב טווח השבוע המבוקש
    connection = database.get_connection()                          # פתיחת חיבור לבסיס הנתונים
    row = connection.execute(                                          # סכימת המחירים של כל התורים שהושלמו בטווח
        "SELECT COALESCE(SUM(price), 0) AS total FROM appointments "
        "WHERE appointment_date BETWEEN ? AND ? AND status = 'בוצע'",
        (start, end)
    ).fetchone()
    connection.close()                                                 # סגירת החיבור
    return row["total"]                                                  # החזרת סך ההכנסה השבועית


def get_monthly_revenue(year, month):
    """מחזיר את סך ההכנסה (סכום המחירים) מתורים שהושלמו בחודש ובשנה נתונים."""
    month_prefix = f"{year:04d}-{month:02d}"                        # בניית תחילית תאריך לחודש המבוקש, למשל '2026-08'
    connection = database.get_connection()                            # פתיחת חיבור לבסיס הנתונים
    row = connection.execute(                                            # סכימת המחירים של כל התורים שהושלמו באותו חודש
        "SELECT COALESCE(SUM(price), 0) AS total FROM appointments "
        "WHERE appointment_date LIKE ? AND status = 'בוצע'",
        (month_prefix + "%",)
    ).fetchone()
    connection.close()                                                   # סגירת החיבור
    return row["total"]                                                    # החזרת סך ההכנסה החודשית


def get_current_month_revenue():
    """נוחות: מחזיר את סך ההכנסה של החודש הקלנדרי הנוכחי."""
    today = datetime.date.today()                        # תאריך היום הנוכחי
    return get_monthly_revenue(today.year, today.month)     # חישוב ההכנסה החודשית לפי השנה והחודש הנוכחיים
