"""
models/customers.py - כל הלוגיקה שקשורה ללקוחות: הוספה, רשימה, מחיקה, שליפת לקוח לפי טלפון
(לצורך קישור אוטומטי בעת הזמנת תור), ושליפת היסטוריית התורים והחשבוניות של לקוח מסוים.
"""

import sys                                  # מודול מערכת, כדי שנוכל לגשת לתיקיית הפרויקט הראשית
import os                                   # מודול נתיבי קבצים
import sqlite3                              # מודול ה-SQLite, כדי לתפוס שגיאת מפתח זר בעת מחיקה
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # מוסיף את תיקיית הפרויקט ל-path

import database  # קובץ הגישה לבסיס הנתונים


def add_customer(full_name, phone, email=None, address=None, id_number=None):
    """מוסיף לקוח חדש לבסיס הנתונים, לאחר בדיקת תקינות בסיסית. מחזיר את מזהה הלקוח החדש.
    id_number (תעודת זהות) הוא אופציונלי, אך נדרש כדי שהלקוח יוכל לעבור אימות בצ'אטבוט."""
    if not full_name or not phone:                          # ולידציה: שם וטלפון הם שדות חובה ללקוח
        raise ValueError("חובה לספק שם מלא וטלפון עבור הלקוח")  # שגיאה ברורה שתוצג למשתמש
    connection = database.get_connection()                    # פתיחת חיבור לבסיס הנתונים
    cursor = connection.execute(                                 # הכנסת רשומת הלקוח החדש לטבלה
        "INSERT INTO customers (full_name, phone, email, address, id_number) VALUES (?, ?, ?, ?, ?)",
        (full_name, phone, email, address, id_number)
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


def search_customers_by_name(partial_name):
    """מחפש לקוחות לפי שם חלקי (למשל שם פרטי בלבד), לצורך זיהוי ראשוני בצ'אטבוט. מחזיר רשימה -
    יכולה להכיל אפס, לקוח אחד, או כמה לקוחות תואמים (למשל כמה לקוחות בשם "רותם")."""
    connection = database.get_connection()                       # פתיחת חיבור לבסיס הנתונים
    like_pattern = f"%{partial_name.strip()}%"                      # תבנית חיפוש: השם החלקי יכול להופיע בכל מקום בשם המלא
    rows = connection.execute(                                       # חיפוש כל הלקוחות שהשם המלא שלהם מכיל את השם החלקי
        "SELECT * FROM customers WHERE full_name LIKE ? ORDER BY full_name", (like_pattern,)
    ).fetchall()
    connection.close()                                              # סגירת החיבור
    return [dict(row) for row in rows]                                # המרת התוצאות לרשימת מילונים


def find_customer_by_id_number(id_number):
    """מחפש לקוח לפי תעודת זהות מדויקת. משמש את הצ'אטבוט לשתי מטרות: (1) אימות מהיר כשלקוח/ה
    מוסר/ת שם ותעודת זהות יחד באותה הודעה, ו-(2) לדעת האם תעודת זהות שהוזנה בכלל רשומה במערכת,
    כדי להציע ללקוח/ה חדש/ה לקבוע תור במקום סתם לומר "לא תואם". מחזיר לקוח או None."""
    digits = "".join(character for character in (id_number or "") if character.isdigit())  # ספרות בלבד, להשוואה עקבית
    if not digits:                                                    # אם לא נשארו ספרות כלל - אין מה לחפש
        return None                                                      # מחזירים None בלי לפנות לבסיס הנתונים
    connection = database.get_connection()                            # פתיחת חיבור לבסיס הנתונים
    row = connection.execute(                                            # חיפוש לקוח עם תעודת הזהות המדויקת הזו
        "SELECT * FROM customers WHERE id_number = ?", (digits,)
    ).fetchone()
    connection.close()                                                   # סגירת החיבור
    return dict(row) if row else None                                     # החזרת מילון אם נמצא, אחרת None


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
