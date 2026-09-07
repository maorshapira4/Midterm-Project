-- schema.sql
-- קובץ זה מגדיר את מבנה בסיס הנתונים של מערכת ניהול התורים לקליניקת הקוסמטיקה.
-- הוא מכיל את כל פקודות ה-CREATE TABLE, ומורץ אוטומטית בכל הפעלה של האפליקציה (database.py).

-- טבלת לקוחות: כל לקוח קבוע שנרשם בקליניקה
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,                -- מזהה ייחודי אוטומטי לכל לקוח
    full_name TEXT NOT NULL,                              -- שם מלא של הלקוח (שדה חובה)
    phone TEXT NOT NULL,                                  -- מספר טלפון של הלקוח (שדה חובה)
    email TEXT NOT NULL,                                  -- כתובת אימייל (שדה חובה) - זהו אמצעי האימות של הלקוח
    created_at TEXT NOT NULL DEFAULT (datetime('now'))    -- תאריך ושעת יצירת הרשומה, נקבע אוטומטית
);
-- הערה: האימייל הוא שדה חובה, והוא זה שמשמש את הצ'אטבוט לאימות זהות הלקוח/ה. לקוח/ה בלי
-- אימייל אינו/ה יכול/ה להיות רשום/ה במערכת, ולכן גם אינו/ה יכול/ה להזדהות בצ'אט.
-- הערה נוספת: אם בסיס הנתונים כבר קיים מריצה קודמת (עם העמודות הישנות id_number ו-address),
-- הפקודה CREATE TABLE IF NOT EXISTS לא תשנה אותו - database.py מטפל בזה במיגרציה ייעודית.

-- טבלת שירותים: כל סוגי הטיפולים שהקליניקה מציעה, עם משך ומחיר ברירת מחדל
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,                 -- מזהה ייחודי לכל שירות
    name TEXT NOT NULL UNIQUE,                             -- שם השירות (למשל "לק ג'ל") - חייב להיות ייחודי
    default_duration_minutes INTEGER NOT NULL,             -- משך ברירת מחדל של הטיפול, בדקות
    default_price REAL NOT NULL                            -- מחיר ברירת מחדל של הטיפול, בשקלים
);

-- טבלת תורים: הלב של המערכת - כל תור שנקבע, ממתין, בוצע או בוטל
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,                  -- מזהה ייחודי לכל תור
    customer_id INTEGER,                                    -- מפתח זר ללקוח קיים (ריק אם עדיין אין רשומת לקוח)
    guest_name TEXT,                                        -- שם לרישום זמני, למקרה שאין עדיין רשומת לקוח
    guest_phone TEXT,                                       -- טלפון לרישום זמני, למקרה שאין עדיין רשומת לקוח
    gender TEXT NOT NULL,                                    -- מגדר בעל/ת התור: 'גבר' או 'אישה'
    appointment_date TEXT NOT NULL,                            -- תאריך התור, בפורמט YYYY-MM-DD
    appointment_time TEXT NOT NULL,                            -- שעת התחלת התור, בפורמט HH:MM
    duration_minutes INTEGER NOT NULL,                          -- משך התור הכולל בדקות (סכום כל השירותים שנבחרו)
    status TEXT NOT NULL DEFAULT 'ממתין',                        -- סטטוס התור: ממתין / בוצע / בוטל
    price REAL NOT NULL,                                         -- מחיר התור הכולל (סכום מחירי כל השירותים שנבחרו)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),           -- תאריך ושעת יצירת הרשומה
    FOREIGN KEY (customer_id) REFERENCES customers(id)              -- קישור לטבלת הלקוחות
);

-- טבלת קישור בין תור לשירותים: כל תור יכול לכלול שירות אחד או יותר (למשל גם לק ג'ל וגם פדיקור)
CREATE TABLE IF NOT EXISTS appointment_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,                    -- מזהה ייחודי לכל שורת קישור
    appointment_id INTEGER NOT NULL,                          -- מפתח זר לתור
    service_id INTEGER NOT NULL,                                -- מפתח זר לשירות שנבחר עבור אותו תור
    FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE CASCADE,  -- אם התור נמחק, קישורי השירותים שלו נמחקים אוטומטית איתו
    FOREIGN KEY (service_id) REFERENCES services(id)                 -- קישור לטבלת השירותים
);

-- טבלת חשבוניות: חשבונית מס שהונפקה עבור לקוח (ולרוב גם עבור תור מסוים)
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,                   -- מזהה ייחודי לכל חשבונית
    invoice_number TEXT NOT NULL UNIQUE,                     -- מספר חשבונית ייחודי, למשל INV-0001
    customer_id INTEGER NOT NULL,                             -- מפתח זר ללקוח שעבורו הונפקה החשבונית
    appointment_id INTEGER,                                     -- מפתח זר לתור המשויך (יכול להיות ריק)
    amount REAL NOT NULL,                                        -- סכום החשבונית, בשקלים
    issue_date TEXT NOT NULL DEFAULT (datetime('now')),           -- תאריך הנפקת החשבונית
    FOREIGN KEY (customer_id) REFERENCES customers(id),             -- קישור לטבלת הלקוחות
    FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE SET NULL  -- אם התור נמחק, החשבונית נשארת אך מנותקת ממנו
);

-- טבלת לידים: פניות ראשוניות שעדיין לא הפכו ללקוחות רשומים
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,                    -- מזהה ייחודי לכל ליד
    full_name TEXT NOT NULL,                                  -- שם מלא של הליד
    phone TEXT NOT NULL,                                       -- מספר טלפון של הליד
    source TEXT,                                                 -- מקור הפנייה (למשל: אינסטגרם, המלצה, טלפון)
    status TEXT NOT NULL DEFAULT 'חדש',                           -- סטטוס הליד: חדש / בטיפול / הפך ללקוח / נדחה
    notes TEXT,                                                    -- הערות חופשיות על הליד
    created_at TEXT NOT NULL DEFAULT (datetime('now'))              -- תאריך ושעת יצירת הרשומה
);
