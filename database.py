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
    """יוצר את כל הטבלאות הנדרשות אם הן עדיין לא קיימות, לפי התוכן של schema.sql, ומריץ מיגרציות
    קטנות עבור בסיסי נתונים ישנים יותר שכבר קיימים מריצה קודמת של הפרויקט."""
    connection = get_connection()                                       # פתיחת חיבור לבסיס הנתונים
    with open(SCHEMA_PATH, "r", encoding="utf-8") as schema_file:        # פתיחת קובץ הסכמה לקריאה בקידוד UTF-8 (בגלל עברית)
        schema_sql = schema_file.read()                                   # קריאת כל תוכן קובץ ה-SQL כמחרוזת אחת
    connection.executescript(schema_sql)                                   # הרצת כל פקודות ה-CREATE TABLE שבקובץ בבת אחת
    connection.commit()                                                     # שמירת השינויים בפועל לקובץ בסיס הנתונים
    _migrate_customers_table(connection)                                  # התאמת מבנה טבלת הלקוחות, אם היא במבנה ישן
    connection.close()                                                       # סגירת החיבור לאחר סיום יצירת הטבלאות


def _migrate_customers_table(connection):
    """מיגרציה למבנה טבלת הלקוחות הנוכחי: אימייל כשדה חובה, בלי תעודת זהות ובלי כתובת.

    למה צריך את זה בכלל? כי "CREATE TABLE IF NOT EXISTS" אינו משנה טבלה שכבר קיימת. בסיס
    נתונים שנוצר בגרסה קודמת של הפרויקט (למשל זה שרץ על השרת החי) נשאר עם המבנה הישן, ולכן
    צריך לבנות את הטבלה מחדש. ב-SQLite אי אפשר פשוט "למחוק עמודה" בגרסאות ישנות, ולכן הדרך
    הבטוחה והנתמכת בכל הגרסאות היא: יצירת טבלה חדשה, העתקת הנתונים הרלוונטיים אליה, מחיקת
    הישנה, ושינוי שם.

    חשוב: לקוח/ה בלי אימייל אינו/ה יכול/ה להתקיים במבנה החדש (האימייל הוא אמצעי האימות),
    ולכן רשומות כאלה אינן מועתקות - יחד עם התורים והחשבוניות שלהן, כדי לא להשאיר רשומות
    "יתומות" שמצביעות ללקוח/ה שכבר לא קיים/ת."""
    columns = {                                                        # שמות העמודות הקיימות כרגע בטבלה
        row["name"] for row in connection.execute("PRAGMA table_info(customers)").fetchall()
    }
    needs_migration = "id_number" in columns or "address" in columns   # מבנה ישן מזוהה לפי העמודות שהוסרו
    if not needs_migration:                                              # הטבלה כבר במבנה הנכון
        return                                                              # אין מה לעשות

    connection.execute("PRAGMA foreign_keys = OFF")     # כיבוי זמני, כדי שאפשר יהיה להחליף את הטבלה
    try:
        # (1) מזהי הלקוחות שאין להם אימייל תקין - הם לא יעברו למבנה החדש
        doomed = [row["id"] for row in connection.execute(
            "SELECT id FROM customers WHERE email IS NULL OR TRIM(email) = ''"
        ).fetchall()]

        if doomed:                                                        # ניקוי כל מה שתלוי בהם
            placeholders = ",".join("?" for _ in doomed)
            connection.execute(f"DELETE FROM appointment_services WHERE appointment_id IN "
                                f"(SELECT id FROM appointments WHERE customer_id IN ({placeholders}))", doomed)
            connection.execute(f"DELETE FROM invoices WHERE customer_id IN ({placeholders})", doomed)
            connection.execute(f"DELETE FROM appointments WHERE customer_id IN ({placeholders})", doomed)

        # (2) בניית הטבלה החדשה, העתקת הנתונים התקינים בלבד, והחלפה
        connection.execute("""
            CREATE TABLE customers_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        connection.execute(
            "INSERT INTO customers_new (id, full_name, phone, email, created_at) "
            "SELECT id, full_name, phone, email, created_at FROM customers "
            "WHERE email IS NOT NULL AND TRIM(email) != ''"
        )
        connection.execute("DROP TABLE customers")                       # מחיקת הטבלה הישנה
        connection.execute("ALTER TABLE customers_new RENAME TO customers")  # והחלפתה בחדשה
        connection.commit()                                                 # שמירת כל המיגרציה יחד
    except Exception:
        connection.rollback()                                            # אם משהו נכשל - לא משאירים מצב חלקי
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")                    # החזרת אכיפת המפתחות הזרים
