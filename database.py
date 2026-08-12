"""
database.py - שכבת הגישה הבסיסית לבסיס הנתונים.
קובץ זה אחראי אך ורק על פתיחת חיבור ל-SQLite ועל יצירת הטבלאות מתוך schema.sql.
כל שאר הקבצים (בתיקיית models) משתמשים בפונקציית get_connection() כדי לגשת לנתונים,
כך שרק הקובץ הזה "יודע" איך בדיוק מתחברים לבסיס הנתונים.
"""

import sqlite3  # מודול פייתון מובנה לעבודה עם בסיסי נתונים מסוג SQLite
import os       # מודול לעבודה עם נתיבי קבצים בצורה שעובדת בכל מערכת הפעלה
import config   # קובץ ההגדרות שלנו, כדי לדעת איפה לשמור את קובץ בסיס הנתונים

# תיקיית הבסיס של הפרויקט - מחושבת לפי מיקום הקובץ הנוכחי, כדי שהנתיב יעבוד תמיד
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # הנתיב המלא לתיקייה שבה נמצא database.py

# נתיב מלא לקובץ בסיס הנתונים עצמו
DB_PATH = os.path.join(BASE_DIR, config.DATABASE_PATH)  # מצרף את שם הקובץ מ-config.py לנתיב הפרויקט

# נתיב מלא לקובץ הסכמה schema.sql
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")  # מצרף את שם קובץ הסכמה לנתיב הפרויקט


def get_connection():
    """פותח ומחזיר חיבור חדש לבסיס הנתונים, עם אפשרות לגשת לתוצאות גם לפי שם עמודה."""
    connection = sqlite3.connect(DB_PATH)              # פתיחת חיבור לקובץ ה-SQLite (הקובץ נוצר אוטומטית אם לא קיים)
    connection.row_factory = sqlite3.Row                # מאפשר לגשת לעמודות תוצאה גם לפי שם (row["name"]) וגם לפי אינדקס
    connection.execute("PRAGMA foreign_keys = ON")        # מפעיל אכיפת מפתחות זרים (foreign keys) בבסיס הנתונים
    return connection                                      # מחזיר את החיבור הפתוח לשימוש הקוד שקרא לפונקציה


def init_db():
    """יוצר את כל הטבלאות הנדרשות אם הן עדיין לא קיימות, לפי התוכן של schema.sql."""
    connection = get_connection()                                       # פתיחת חיבור לבסיס הנתונים
    with open(SCHEMA_PATH, "r", encoding="utf-8") as schema_file:        # פתיחת קובץ הסכמה לקריאה בקידוד UTF-8 (בגלל עברית)
        schema_sql = schema_file.read()                                   # קריאת כל תוכן קובץ ה-SQL כמחרוזת אחת
    connection.executescript(schema_sql)                                   # הרצת כל פקודות ה-CREATE TABLE שבקובץ בבת אחת
    connection.commit()                                                     # שמירת השינויים בפועל לקובץ בסיס הנתונים
    connection.close()                                                       # סגירת החיבור לאחר סיום יצירת הטבלאות
