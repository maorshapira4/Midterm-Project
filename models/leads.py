"""
models/leads.py - כל הלוגיקה שקשורה לניהול לידים (פניות ראשוניות): הוספה, רשימה, עדכון סטטוס,
והמרת ליד ללקוח רשום במערכת (יוצר רשומת לקוח חדשה מתוך נתוני הליד).
"""

import sys                                  # מודול מערכת, כדי שנוכל לגשת לתיקיית הפרויקט הראשית
import os                                   # מודול נתיבי קבצים
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # מוסיף את תיקיית הפרויקט ל-path

import database             # קובץ הגישה לבסיס הנתונים
import models.customers as customers  # מודול הלקוחות, לשימוש בעת המרת ליד ללקוח

LEAD_STATUSES = ("חדש", "בטיפול", "הפך ללקוח", "נדחה")  # רשימת כל סטטוסי הליד האפשריים במערכת


def add_lead(full_name, phone, source=None, notes=None):
    """מוסיף ליד חדש לבסיס הנתונים, לאחר בדיקת תקינות בסיסית. מחזיר את מזהה הליד החדש."""
    if not full_name or not phone:                            # ולידציה: שם וטלפון הם שדות חובה לליד
        raise ValueError("חובה לספק שם מלא וטלפון עבור הליד")    # שגיאה ברורה שתוצג למשתמש
    connection = database.get_connection()                      # פתיחת חיבור לבסיס הנתונים
    cursor = connection.execute(                                   # הכנסת רשומת הליד החדש לטבלה
        "INSERT INTO leads (full_name, phone, source, notes) VALUES (?, ?, ?, ?)",
        (full_name, phone, source, notes)
    )
    connection.commit()                                              # שמירת הליד החדש בפועל
    new_id = cursor.lastrowid                                          # שליפת המזהה שקיבל הליד החדש
    connection.close()                                                   # סגירת החיבור
    return new_id                                                         # החזרת מזהה הליד החדש


def list_leads():
    """מחזיר רשימה של כל הלידים במערכת, מהחדש לישן."""
    connection = database.get_connection()                        # פתיחת חיבור לבסיס הנתונים
    rows = connection.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()  # שליפת כל הלידים
    connection.close()                                               # סגירת החיבור
    return [dict(row) for row in rows]                                 # המרת התוצאות לרשימת מילונים


def get_lead(lead_id):
    """מחזיר ליד בודד לפי מזהה, או None אם לא נמצא."""
    connection = database.get_connection()                        # פתיחת חיבור לבסיס הנתונים
    row = connection.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()  # שליפת הליד המבוקש
    connection.close()                                               # סגירת החיבור
    return dict(row) if row else None                                 # החזרת מילון אם נמצא, אחרת None


def update_lead_status(lead_id, new_status):
    """מעדכן את הסטטוס של ליד קיים (חדש / בטיפול / הפך ללקוח / נדחה)."""
    if new_status not in LEAD_STATUSES:                             # ולידציה: מותרים רק ערכי סטטוס חוקיים
        raise ValueError("סטטוס ליד לא חוקי")                          # שגיאה ברורה אם התקבל ערך לא צפוי
    connection = database.get_connection()                           # פתיחת חיבור לבסיס הנתונים
    connection.execute(                                                 # עדכון שדה הסטטוס של הליד המבוקש
        "UPDATE leads SET status = ? WHERE id = ?", (new_status, lead_id)
    )
    connection.commit()                                                  # שמירת השינוי בפועל
    connection.close()                                                     # סגירת החיבור


def convert_lead_to_customer(lead_id):
    """הופך ליד קיים ללקוח רשום: יוצר רשומת לקוח חדשה מתוך שם וטלפון הליד, ומעדכן את סטטוס הליד."""
    lead = get_lead(lead_id)                                       # שליפת פרטי הליד המבוקש
    if not lead:                                                     # אם הליד לא נמצא בבסיס הנתונים
        raise ValueError("הליד לא נמצא")                               # שגיאה ברורה שתוצג למשתמש
    new_customer_id = customers.add_customer(                          # יצירת רשומת לקוח חדשה מנתוני הליד
        full_name=lead["full_name"], phone=lead["phone"]
    )
    update_lead_status(lead_id, "הפך ללקוח")                             # עדכון סטטוס הליד ל"הפך ללקוח"
    return new_customer_id                                                # החזרת מזהה הלקוח החדש שנוצר
