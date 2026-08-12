"""
models/customers.py - כל הלוגיקה שקשורה ללקוחות: הוספה, רשימה, מחיקה, שליפת לקוח לפי טלפון
(לצורך קישור אוטומטי בעת הזמנת תור), ושליפת היסטוריית התורים והחשבוניות של לקוח מסוים.
"""

import sys                                  # מודול מערכת, כדי שנוכל לגשת לתיקיית הפרויקט הראשית
import os                                   # מודול נתיבי קבצים
import sqlite3                              # מודול ה-SQLite, כדי לתפוס שגיאת מפתח זר בעת מחיקה
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # מוסיף את תיקיית הפרויקט ל-path

import database  # קובץ הגישה לבסיס הנתונים


def add_customer(full_name, phone, email=None, address=None):
    """מוסיף לקוח חדש לבסיס הנתונים, לאחר בדיקת תקינות בסיסית. מחזיר את מזהה הלקוח החדש."""
    if not full_name or not phone:                          # ולידציה: שם וטלפון הם שדות חובה ללקוח
        raise ValueError("חובה לספק שם מלא וטלפון עבור הלקוח")  # שגיאה ברורה שתוצג למשתמש
    connection = database.get_connection()                    # פתיחת חיבור לבסיס הנתונים
    cursor = connection.execute(                                 # הכנסת רשומת הלקוח החדש לטבלה
        "INSERT INTO customers (full_name, phone, email, address) VALUES (?, ?, ?, ?)",
        (full_name, phone, email, address)
    )
    connection.commit()                                            # שמירת הלקוח החדש בפועל
    new_id = cursor.lastrowid                                        # שליפת המזהה שקיבל הלקוח החדש
    connection.close()                                                 # סגירת החיבור
    return new_id                                                        # החזרת מזהה הלקוח החדש לקוד הקורא


def list_customers():
    """מחזיר רשימה של כל הלקוחות הרשומים במערכת, ממוינים לפי שם."""
    connection = database.get_connection()                       # פתיחת חיבור לבסיס הנתונים
    rows = connection.execute("SELECT * FROM customers ORDER BY full_name").fetchall()  # שליפת כל הלקוחות
    connection.close()                                              # סגירת החיבור
    return [dict(row) for row in rows]                                # המרת התוצאות לרשימת מילונים נוחה


def get_customer(customer_id):
    """מחזיר לקוח בודד לפי מזהה, או None אם לא נמצא."""
    connection = database.get_connection()                       # פתיחת חיבור לבסיס הנתונים
    row = connection.execute(                                       # שליפת הלקוח המבוקש לפי מזהה
        "SELECT * FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    connection.close()                                              # סגירת החיבור
    return dict(row) if row else None                                # החזרת מילון אם נמצא, אחרת None


def find_customer_by_phone(phone):
    """מחפש לקוח קיים לפי מספר טלפון - משמש לקישור אוטומטי בעת הזמנת תור עצמית. מחזיר לקוח או None."""
    connection = database.get_connection()                       # פתיחת חיבור לבסיס הנתונים
    row = connection.execute(                                       # חיפוש לקוח לפי מספר הטלפון שהוזן
        "SELECT * FROM customers WHERE phone = ?", (phone,)
    ).fetchone()
    connection.close()                                              # סגירת החיבור
    return dict(row) if row else None                                # החזרת מילון אם נמצא, אחרת None


def delete_customer(customer_id):
    """מוחק לקוח מבסיס הנתונים לפי מזהה, לפי בקשת המנהל/ת. אם ללקוח יש תורים ו/או חשבוניות
    קיימות, המחיקה נחסמת (כדי לא לאבד היסטוריה), ומועלית שגיאה ברורה במקום."""
    connection = database.get_connection()                       # פתיחת חיבור לבסיס הנתונים
    try:
        connection.execute("DELETE FROM customers WHERE id = ?", (customer_id,))  # ניסיון מחיקת הלקוח
        connection.commit()                                              # שמירת המחיקה בפועל
    except sqlite3.IntegrityError:                                          # אם יש רשומות תלויות (תורים/חשבוניות)
        connection.rollback()                                                # ביטול הניסיון, לא לשנות כלום
        raise ValueError("לא ניתן למחוק לקוח עם תורים ו/או חשבוניות קיימות במערכת")  # שגיאה ברורה למשתמש
    finally:
        connection.close()                                                     # סגירת החיבור בכל מקרה


def get_customer_appointments(customer_id):
    """מחזיר את כל התורים (ההיסטוריה) של לקוח מסוים, כולל רשימת שמות השירותים בכל תור,
    ממוינים מהחדש לישן."""
    connection = database.get_connection()                       # פתיחת חיבור לבסיס הנתונים
    rows = connection.execute(                                      # שליפת כל התורים של הלקוח, כולל שמות השירותים
        "SELECT appointments.*, GROUP_CONCAT(services.name, ', ') AS service_names "
        "FROM appointments "
        "LEFT JOIN appointment_services ON appointment_services.appointment_id = appointments.id "
        "LEFT JOIN services ON appointment_services.service_id = services.id "
        "WHERE appointments.customer_id = ? "
        "GROUP BY appointments.id "
        "ORDER BY appointments.appointment_date DESC, appointments.appointment_time DESC",
        (customer_id,)
    ).fetchall()
    connection.close()                                              # סגירת החיבור
    return [dict(row) for row in rows]                                # המרת התוצאות לרשימת מילונים
