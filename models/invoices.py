"""
models/invoices.py - כל הלוגיקה שקשורה לחשבוניות: יצירת חשבונית עם מספור ייחודי אוטומטי,
שליפת חשבונית בודדת, ושליפת היסטוריית החשבוניות של לקוח מסוים.
"""

import sys                                  # מודול מערכת, כדי שנוכל לגשת לתיקיית הפרויקט הראשית
import os                                   # מודול נתיבי קבצים
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # מוסיף את תיקיית הפרויקט ל-path

import database  # קובץ הגישה לבסיס הנתונים
import config    # קובץ ההגדרות הכלליות (כאן משתמשים בתחילית מספר החשבונית)


def _generate_invoice_number(connection):
    """מייצר מספר חשבונית ייחודי חדש, בפורמט 'INV-0001', לפי כמות החשבוניות הקיימות עד כה."""
    row = connection.execute("SELECT COUNT(*) AS total FROM invoices").fetchone()  # ספירת כל החשבוניות הקיימות
    next_number = row["total"] + 1                                                    # המספר הסידורי הבא בתור
    return f"{config.INVOICE_PREFIX}-{next_number:04d}"                                 # בניית המחרוזת עם אפסים מובילים


def create_invoice(customer_id, appointment_id, amount):
    """יוצר חשבונית חדשה עבור לקוח (ולרוב גם עבור תור מסוים), עם מספר ייחודי אוטומטי."""
    if not customer_id or amount is None:                      # ולידציה: לקוח וסכום הם שדות חובה
        raise ValueError("חובה לבחור לקוח ולציין סכום עבור החשבונית")  # שגיאה ברורה שתוצג למשתמש
    connection = database.get_connection()                       # פתיחת חיבור לבסיס הנתונים
    invoice_number = _generate_invoice_number(connection)          # ייצור מספר חשבונית ייחודי חדש
    cursor = connection.execute(                                     # הכנסת רשומת החשבונית החדשה לטבלה
        "INSERT INTO invoices (invoice_number, customer_id, appointment_id, amount) VALUES (?, ?, ?, ?)",
        (invoice_number, customer_id, appointment_id, amount)
    )
    connection.commit()                                                # שמירת החשבונית החדשה בפועל
    new_id = cursor.lastrowid                                            # שליפת המזהה שקיבלה החשבונית החדשה
    connection.close()                                                     # סגירת החיבור
    return new_id                                                           # החזרת מזהה החשבונית החדשה


def get_invoice(invoice_id):
    """מחזיר חשבונית בודדת לפי מזהה, כולל שם הלקוח, או None אם לא נמצאה."""
    connection = database.get_connection()                       # פתיחת חיבור לבסיס הנתונים
    row = connection.execute(                                       # שליפת החשבונית המבוקשת, כולל שם הלקוח
        "SELECT invoices.*, customers.full_name AS customer_name "
        "FROM invoices LEFT JOIN customers ON invoices.customer_id = customers.id "
        "WHERE invoices.id = ?",
        (invoice_id,)
    ).fetchone()
    connection.close()                                              # סגירת החיבור
    return dict(row) if row else None                                # החזרת מילון אם נמצאה, אחרת None


def list_invoices_for_customer(customer_id):
    """מחזיר את כל החשבוניות שהונפקו ללקוח מסוים, ממוינות מהחדשה לישנה."""
    connection = database.get_connection()                       # פתיחת חיבור לבסיס הנתונים
    rows = connection.execute(                                      # שליפת כל החשבוניות של הלקוח
        "SELECT * FROM invoices WHERE customer_id = ? ORDER BY issue_date DESC",
        (customer_id,)
    ).fetchall()
    connection.close()                                              # סגירת החיבור
    return [dict(row) for row in rows]                                # המרת התוצאות לרשימת מילונים
